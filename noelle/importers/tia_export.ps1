<#
  Noelle - TIA Portal Openness exporter (64-bit Windows PowerShell, .NET 4.8).
  Opens a TIA project WITHOUT user interface and exports to SimaticML XML:
    <out>\<HMI device>\screens\*.xml        HMI screens (WinCC Comfort/Advanced/Professional where supported)
    <out>\<HMI device>\templates\*.xml      screen templates
    <out>\<HMI device>\tags\*.xml           HMI tag tables
    <out>\<HMI device>\connections.json     HMI connections
    <out>\<PLC>\tags\*.xml                  PLC tag tables
    <out>\<PLC>\db\*.xml                    data blocks (memory layout optimized/standard, offsets)
    <out>\summary.json
  The caller passes a COPY of the project; the project is closed without saving.
  Requires membership in the Windows group "Siemens TIA Openness".
#>
param([string]$Project, [string]$Out, [string]$Version = "V17", [string]$Device = "")
$ErrorActionPreference = "Stop"
$portal = Get-ChildItem "C:\Program Files\Siemens\Automation" -Directory | Where-Object { $_.Name -eq "Portal $Version" } | Select-Object -First 1
if (-not $portal) { throw "TIA Portal $Version not installed" }
$api = Join-Path $portal.FullName "PublicAPI\$Version"
if (-not (Test-Path (Join-Path $api "Siemens.Engineering.dll"))) { throw "Openness API not found in $api" }
# assembly resolver in C# (a PowerShell scriptblock handler can re-enter itself -> StackOverflow)
Add-Type -TypeDefinition @"
using System; using System.IO; using System.Reflection;
public static class GfResolver {
  static string[] dirs; [ThreadStatic] static bool busy;
  public static void Install(string[] d) { dirs = d; AppDomain.CurrentDomain.AssemblyResolve += Resolve; }
  static Assembly Resolve(object s, ResolveEventArgs e) {
    if (busy) return null;
    string n = new AssemblyName(e.Name).Name;
    if (n.EndsWith(".resources", StringComparison.OrdinalIgnoreCase) || n.EndsWith(".XmlSerializers", StringComparison.OrdinalIgnoreCase)) return null;
    busy = true;
    try { foreach (var d in dirs) { var p = Path.Combine(d, n + ".dll"); if (File.Exists(p)) return Assembly.LoadFrom(p); } return null; }
    finally { busy = false; }
  }
}
"@
[GfResolver]::Install(@($api, (Join-Path $portal.FullName "Bin\PublicAPI")))
$refs = @((Join-Path $api "Siemens.Engineering.dll"), (Join-Path $api "Siemens.Engineering.Hmi.dll"), "System.Web.Extensions", "System.Core", "Microsoft.CSharp")
Add-Type -ReferencedAssemblies $refs -TypeDefinition @"
using System; using System.Collections.Generic; using System.IO; using System.Linq; using System.Web.Script.Serialization;
using Siemens.Engineering; using Siemens.Engineering.HW; using Siemens.Engineering.HW.Features; using Siemens.Engineering.SW;
using Siemens.Engineering.SW.Tags; using Siemens.Engineering.SW.Blocks; using Siemens.Engineering.Hmi; using Siemens.Engineering.Hmi.Screen; using Siemens.Engineering.Hmi.Tag;

public static class GfTia {
  static List<object> log = new List<object>();
  static string Safe(string s) { foreach (var c in Path.GetInvalidFileNameChars()) s = s.Replace(c, '_'); return s; }
  static void Exp(dynamic item, string dir, string name, string kind) {
    Directory.CreateDirectory(dir);
    var f = new FileInfo(Path.Combine(dir, Safe(name) + ".xml"));
    if (f.Exists) f.Delete();
    try { item.Export(f, ExportOptions.WithDefaults); log.Add(new Dictionary<string, object> { {"kind", kind}, {"name", name}, {"file", f.FullName}, {"ok", true} }); }
    catch (Exception e) { log.Add(new Dictionary<string, object> { {"kind", kind}, {"name", name}, {"ok", false}, {"error", e.Message} }); }
  }
  // folder traversal with dynamic: works across Openness versions (User/System folder types differ)
  static void Walk(dynamic folder, string items, string sub, string dir, string kind) {
    try { foreach (dynamic s in (System.Collections.IEnumerable)Get(folder, items)) Exp(s, dir, (string)s.Name, kind); } catch (Exception e) { log.Add(kind + " list: " + e.Message); }
    try { foreach (dynamic c in (System.Collections.IEnumerable)Get(folder, sub)) Walk(c, items, sub, dir, kind); } catch { }
  }
  static object Get(object o, string prop) { return o.GetType().GetProperty(prop).GetValue(o, null); }
  static void Blocks(dynamic g, string dir) {
    foreach (dynamic b in g.Blocks) {
      if (!(b is DataBlock)) continue;
      string layout = ""; try { layout = Convert.ToString(b.GetAttribute("MemoryLayout")); } catch { }
      Exp(b, dir, (string)b.Name, "db:" + layout);
    }
    foreach (dynamic c in g.Groups) Blocks(c, dir);
  }

  static void Item(DeviceItem di, string outDir, string filter) {
    var sc = di.GetService<SoftwareContainer>();
    if (sc != null && sc.Software != null) log.Add(new Dictionary<string, object> { {"kind", "software"}, {"item", di.Name}, {"type", sc.Software.GetType().FullName}, {"name", Convert.ToString(((dynamic)sc.Software).Name)} });
    if (sc != null) {
      var hmi = sc.Software as HmiTarget;
      if (hmi != null && (filter == "" || hmi.Name == filter || di.Name == filter)) {
        string d = Path.Combine(outDir, Safe(hmi.Name));
        try { Walk(hmi.ScreenFolder, "Screens", "Folders", Path.Combine(d, "screens"), "screen"); } catch (Exception e) { log.Add("screens " + hmi.Name + ": " + e.Message); }
        try { Walk(hmi.ScreenTemplateFolder, "ScreenTemplates", "Folders", Path.Combine(d, "templates"), "template"); } catch (Exception e) { log.Add("templates " + hmi.Name + ": " + e.Message); }
        try { Walk(hmi.TagFolder, "TagTables", "Folders", Path.Combine(d, "tags"), "hmitags"); } catch (Exception e) { log.Add("hmi tags " + hmi.Name + ": " + e.Message); }
        try {
          var cons = new List<object>();
          foreach (var c in hmi.Connections) { var a = new Dictionary<string, object> { {"Name", c.Name} };
            foreach (var an in new[] { "Partner", "CommunicationDriver", "Station", "Node", "Address", "Rack", "Slot", "HostAddress" }) { try { a[an] = Convert.ToString(c.GetAttribute(an)); } catch { } }
            cons.Add(a); }
          Directory.CreateDirectory(d);
          File.WriteAllText(Path.Combine(d, "connections.json"), new JavaScriptSerializer().Serialize(cons));
        } catch (Exception e) { log.Add("connections " + hmi.Name + ": " + e.Message); }
        log.Add(new Dictionary<string, object> { {"kind", "hmi"}, {"name", hmi.Name}, {"dir", d} });
      }
      var plc = sc.Software as PlcSoftware;
      if (plc != null) {
        string d = Path.Combine(outDir, "PLC_" + Safe(plc.Name));
        try { Walk(plc.TagTableGroup, "TagTables", "Groups", Path.Combine(d, "tags"), "plctags"); } catch (Exception e) { log.Add("plc tags " + plc.Name + ": " + e.Message); }
        try { Blocks(plc.BlockGroup, Path.Combine(d, "db")); } catch (Exception e) { log.Add("plc blocks " + plc.Name + ": " + e.Message); }
        try { Walk(plc.TypeGroup, "Types", "Groups", Path.Combine(d, "types"), "udt"); } catch (Exception e) { log.Add("plc types " + plc.Name + ": " + e.Message); }
        var info = new Dictionary<string, object> { {"kind", "plc"}, {"name", plc.Name}, {"dir", d} };
        try { info["device"] = di.Name; } catch { }
        log.Add(info);
      }
    }
    foreach (DeviceItem c in di.DeviceItems) Item(c, outDir, filter);
  }

  static void Devices(DeviceComposition devs, string outDir, string filter) {
    foreach (Device d in devs) {
      string ti = ""; try { ti = Convert.ToString(d.TypeIdentifier); } catch { }
      log.Add(new Dictionary<string, object> { {"kind", "device"}, {"name", d.Name}, {"type", ti} });
      foreach (DeviceItem di in d.DeviceItems) Item(di, outDir, filter);
    }
  }
  static void Groups(DeviceUserGroupComposition gs, string outDir, string filter) { foreach (DeviceUserGroup g in gs) { Devices(g.Devices, outDir, filter); Groups(g.Groups, outDir, filter); } }

  public static string Run(string project, string outDir, string filter) {
    Directory.CreateDirectory(outDir);
    using (var tia = new TiaPortal(TiaPortalMode.WithoutUserInterface)) {
      Project p = tia.Projects.Open(new FileInfo(project));
      try {
        Devices(p.Devices, outDir, filter);
        try { Devices(p.UngroupedDevicesGroup.Devices, outDir, filter); } catch { }
        Groups(p.DeviceGroups, outDir, filter);
      } finally { p.Close(); }
    }
    var js = new JavaScriptSerializer(); js.MaxJsonLength = int.MaxValue;
    string s = js.Serialize(log);
    File.WriteAllText(Path.Combine(outDir, "summary.json"), s);
    return s;
  }
}
"@
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[GfTia]::Run($Project, $Out, $Device)
