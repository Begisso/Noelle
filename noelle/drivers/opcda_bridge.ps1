<#
  Noelle OPC DA bridge - runs in 32-bit Windows PowerShell because the OPC
  Automation wrapper (OPCDAAuto.dll) is a 32-bit COM server. Talks JSON lines on
  stdin/stdout with the Python OPC DA driver.

  Works with any OPC DA 2.0 server: Cogent DataHub (Cogent.DataHub.1), ICONICS,
  Kepware, WinCC, CoDeSys ... An early-bound interop assembly is generated once
  from the type library and cached in %LOCALAPPDATA%\Noelle.
#>
param([string]$Server = "Cogent.DataHub.1", [string]$Node = "", [int]$Rate = 250)
$ErrorActionPreference = "Stop"
$cache = Join-Path $env:LOCALAPPDATA "Noelle"
New-Item -ItemType Directory -Force $cache | Out-Null
$interop = Join-Path $cache "Interop.OPCAutomation.dll"

if (-not (Test-Path $interop)) {
  Add-Type -TypeDefinition @"
using System; using System.Reflection; using System.Reflection.Emit; using System.Runtime.InteropServices; using System.Runtime.InteropServices.ComTypes;
public class GfSink : ITypeLibImporterNotifySink { public void ReportEvent(ImporterEventKind k,int c,string m){} public Assembly ResolveRef(object t){ return null; } }
public static class GfMk {
  [DllImport("oleaut32.dll", CharSet=CharSet.Unicode, PreserveSig=false)] static extern void LoadTypeLibEx(string f, int kind, out ITypeLib tl);
  public static void Run(string dll, string dir) {
    ITypeLib tl; LoadTypeLibEx(dll, 2, out tl);
    Environment.CurrentDirectory = dir;
    AssemblyBuilder ab = new TypeLibConverter().ConvertTypeLibToAssembly(tl, "Interop.OPCAutomation.dll", TypeLibImporterFlags.SafeArrayAsSystemArray, new GfSink(), null, null, "OPCAutomation", null);
    ab.Save("Interop.OPCAutomation.dll");
  } }
"@
  $dll = Join-Path $env:windir "SysWOW64\OPCDAAuto.dll"
  if (-not (Test-Path $dll)) { $dll = Join-Path $env:windir "System32\OPCDAAuto.dll" }
  [GfMk]::Run($dll, $cache)
}

Add-Type -Path $interop
Add-Type -ReferencedAssemblies $interop, "System.Web.Extensions" -TypeDefinition @"
using System; using System.Collections.Generic; using System.Collections.Concurrent; using System.IO; using System.Text; using System.Threading;
using System.Web.Script.Serialization; using OPCAutomation;
public class GfBridge {
  OPCServer srv; OPCGroup grp; int rate;
  Dictionary<int,OPCItem> items = new Dictionary<int,OPCItem>();
  Dictionary<int,object> last = new Dictionary<int,object>();
  Dictionary<int,int> lastQ = new Dictionary<int,int>();
  BlockingCollection<string> inbox = new BlockingCollection<string>();
  JavaScriptSerializer js = new JavaScriptSerializer();
  StreamWriter outw;
  public GfBridge(int rate){ this.rate = rate; js.MaxJsonLength = int.MaxValue;
    outw = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)); outw.AutoFlush = false; }
  void Send(object o){ lock(outw){ outw.Write(js.Serialize(o)); outw.Write("\n"); outw.Flush(); } }
  bool Same(object a, object b){
    if (a == null || b == null) return a == b;
    if (a is List<object> || b is List<object>) return js.Serialize(a) == js.Serialize(b);
    return a.Equals(b);
  }
  static object V(object v){
    if (v == null) return null;
    if (v is DateTime) return ((DateTime)v).ToString("yyyy-MM-dd HH:mm:ss.fff");
    if (v is Array) { var a = (Array)v; var sb = new List<object>(); foreach (var x in a) { sb.Add(V(x)); if (sb.Count>1000) break; } return sb; }
    if (v is decimal) return Convert.ToDouble(v);
    if (v is bool || v is string || v is double || v is float || v is int || v is short || v is long || v is byte || v is sbyte || v is ushort || v is uint || v is ulong) return v;
    return v.ToString();
  }
  public void Run(string server, string node){
    var reader = new Thread(() => { var r = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false)); string l; while ((l = r.ReadLine()) != null) inbox.Add(l); inbox.Add("{\"op\":\"exit\"}"); });
    reader.IsBackground = true; reader.Start();
    try {
      srv = new OPCServerClass(); srv.Connect(server, node);
      grp = srv.OPCGroups.Add("gf"); grp.UpdateRate = rate; grp.IsActive = true; grp.IsSubscribed = false;
      Send(new Dictionary<string,object>{{"op","connected"},{"server",server}});
    } catch (Exception e) { Send(new Dictionary<string,object>{{"op","fatal"},{"msg","connect " + server + ": " + e.Message}}); return; }
    var next = DateTime.UtcNow;
    while (true) {
      string line;
      int wait = Math.Max(0, (int)(next - DateTime.UtcNow).TotalMilliseconds);
      while (inbox.TryTake(out line, wait)) { if (!Handle(line)) return; wait = 0; }
      if (DateTime.UtcNow >= next) { next = DateTime.UtcNow.AddMilliseconds(rate); try { Poll(); } catch (Exception e) { Send(new Dictionary<string,object>{{"op","fatal"},{"msg","poll: " + e.Message}}); return; } }
    }
  }
  bool Handle(string line){
    Dictionary<string,object> m;
    try { m = js.Deserialize<Dictionary<string,object>>(line); } catch { return true; }
    string op = (string)m["op"]; object id = m.ContainsKey("id") ? m["id"] : null;
    try {
      if (op == "exit") { try { srv.Disconnect(); } catch {} return false; }
      if (op == "add") {
        var bad = new List<object>();
        foreach (object o in (System.Collections.ArrayList)m["items"]) { var p = (System.Collections.ArrayList)o; int h = Convert.ToInt32(p[0]); string iid = (string)p[1];
          try { items[h] = grp.OPCItems.AddItem(iid, h); last.Remove(h); lastQ.Remove(h); } catch (Exception e) { bad.Add(new object[]{h, e.Message}); } }
        if (bad.Count > 0) Send(new Dictionary<string,object>{{"op","bad"},{"items",bad}});
      } else if (op == "remove") {
        foreach (object o in (System.Collections.ArrayList)m["items"]) { int h = Convert.ToInt32(o); OPCItem it;
          if (items.TryGetValue(h, out it)) { try { Array sh = new int[]{0, it.ServerHandle}; Array errs; grp.OPCItems.Remove(1, ref sh, out errs); } catch {} items.Remove(h); last.Remove(h); lastQ.Remove(h); } }
      } else if (op == "write") {
        int h = Convert.ToInt32(m["h"]); object val = m["value"]; if (val is decimal) val = Convert.ToDouble(val);
        items[h].Write(val);
        Send(new Dictionary<string,object>{{"op","res"},{"id",id},{"ok",true}});
      } else if (op == "browse") {
        var b = srv.CreateBrowser(); b.MoveToRoot();
        string path = m.ContainsKey("path") ? (string)m["path"] : "";
        if (!String.IsNullOrEmpty(path)) foreach (var part in path.Split('/')) b.MoveDown(part);
        var res = new List<object>();
        b.ShowBranches(); for (int i=1;i<=b.Count;i++){ string n = b.Item(i); res.Add(new Dictionary<string,object>{{"name",n},{"path", String.IsNullOrEmpty(path)? n : path + "/" + n},{"branch",true}}); }
        b.ShowLeafs(false); for (int i=1;i<=b.Count && i<=20000;i++){ string n = b.Item(i); res.Add(new Dictionary<string,object>{{"name",n},{"item", b.GetItemID(n)},{"branch",false}}); }
        Send(new Dictionary<string,object>{{"op","res"},{"id",id},{"ok",true},{"result",res}});
      } else if (op == "servers") {
        object names = srv.GetOPCServers(); Send(new Dictionary<string,object>{{"op","res"},{"id",id},{"ok",true},{"result",V(names)}});
      }
    } catch (Exception e) { if (id != null) Send(new Dictionary<string,object>{{"op","res"},{"id",id},{"ok",false},{"error",e.Message}}); }
    return true;
  }
  void Poll(){
    if (items.Count == 0) return;
    var keys = new List<int>(items.Keys);
    var changes = new List<object>();
    for (int k = 0; k < keys.Count; k += 1000) {
      int n = Math.Min(1000, keys.Count - k);
      Array sh = Array.CreateInstance(typeof(int), new int[]{n+1}, new int[]{0});
      for (int i=0;i<n;i++) sh.SetValue(items[keys[k+i]].ServerHandle, i+1);
      Array vals, errs; object qs, ts;
      grp.SyncRead((short)OPCDataSource.OPCCache, n, ref sh, out vals, out errs, out qs, out ts);
      Array qa = (Array)qs; Array ta = (Array)ts;
      int vb = vals.GetLowerBound(0), qb = qa.GetLowerBound(0), tb = ta.GetLowerBound(0);
      for (int i=0;i<n;i++) {
        int h = keys[k+i]; object v = V(vals.GetValue(vb+i)); int q = Convert.ToInt32(qa.GetValue(qb+i));
        object pv; int pq;
        bool same = last.TryGetValue(h, out pv) && lastQ.TryGetValue(h, out pq) && pq == q && Same(pv, v);
        if (!same) { last[h] = v; lastQ[h] = q; changes.Add(new object[]{h, v, q}); }
      }
    }
    if (changes.Count > 0) Send(new Dictionary<string,object>{{"op","d"},{"v",changes}});
  }
}
"@
[GfBridge]::new($Rate).Run($Server, $Node)
