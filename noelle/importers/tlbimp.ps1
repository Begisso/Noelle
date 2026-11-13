<# Generate an early-bound .NET interop assembly from a COM type library (no Windows SDK needed).
   Usage: powershell -File tlbimp.ps1 -Tlb <file.tlb|.dll|.exe> -Out <dir> -Name <Interop.X.dll> -Namespace <Ns> #>
param([string]$Tlb, [string]$Out, [string]$Name, [string]$Namespace)
$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @"
using System; using System.IO; using System.Reflection; using System.Reflection.Emit; using System.Runtime.InteropServices;
using CT = System.Runtime.InteropServices.ComTypes;
public class GfTlbSink : ITypeLibImporterNotifySink {
  public string Dir;
  public void ReportEvent(ImporterEventKind k, int c, string m) { }
  public Assembly ResolveRef(object t) {
    var tl = (CT.ITypeLib)t;
    string name = Marshal.GetTypeLibName(tl);
    if (name == "stdole") { try { return Assembly.Load("stdole, Version=7.0.3300.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a"); } catch { } }
    string file = "Interop." + name + ".dll";
    string full = Path.Combine(Dir, file);
    if (File.Exists(full)) return Assembly.LoadFrom(full);
    var ab = new TypeLibConverter().ConvertTypeLibToAssembly(t, file, TypeLibImporterFlags.SafeArrayAsSystemArray, this, null, null, name, null);
    ab.Save(file);
    return ab;
  }
}
public static class GfTlb {
  [DllImport("oleaut32.dll", CharSet=CharSet.Unicode, PreserveSig=false)] static extern void LoadTypeLibEx(string f, int kind, out CT.ITypeLib tl);
  public static string Run(string tlb, string dir, string name, string ns) {
    CT.ITypeLib tl; LoadTypeLibEx(tlb, 0, out tl);
    Environment.CurrentDirectory = dir;
    var sink = new GfTlbSink(); sink.Dir = dir;
    var ab = new TypeLibConverter().ConvertTypeLibToAssembly(tl, name, TypeLibImporterFlags.SafeArrayAsSystemArray, sink, null, null, ns, null);
    ab.Save(name);
    return Path.Combine(dir, name);
  }
}
"@
New-Item -ItemType Directory -Force $Out | Out-Null
[GfTlb]::Run($Tlb, $Out, $Name, $Namespace)
