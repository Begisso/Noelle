<#
  Noelle - GraphWorX32 (.gdf) exporter. Runs in 32-bit Windows PowerShell.
  Drives a private GraphWorX32 instance through COM automation (Gwx32.Display),
  dumps every object + dynamic to JSON and optionally renders bitmaps:
    <out>\<name>.json        object tree (all properties of all interfaces)
    <out>\<name>.ref.png     GraphWorX rendering of the whole display (visual identity reference)
    <out>\<name>.bg.png      static artwork only (objects without dynamics), for hybrid import
  A watchdog answers "No" to "save changes?" prompts and logs any other dialog of OUR
  GraphWorX process only. Files are never saved.
#>
param([string]$Files, [string]$Out, [switch]$Snapshots, [switch]$Bake)
$ErrorActionPreference = "Stop"
Add-Type -ReferencedAssemblies "Microsoft.CSharp", "System.Core", "System.Web.Extensions", "System.Drawing" -TypeDefinition @"
using System; using System.Collections.Generic; using System.Diagnostics; using System.IO; using System.Linq; using System.Reflection;
using System.Runtime.InteropServices; using System.Text; using System.Threading; using System.Web.Script.Serialization; using System.Drawing;
using CT = System.Runtime.InteropServices.ComTypes;

[ComImport, Guid("00020400-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IDispatchInfo { [PreserveSig] int GetTypeInfoCount(out uint n); [PreserveSig] int GetTypeInfo(uint i, uint lcid, out CT.ITypeInfo ti); }

public static class GwxExport {
  // ------------------------------------------------------------------ dialog watchdog
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
  const uint BM_CLICK = 0x00F5, WM_CLOSE = 0x0010;
  static uint gwxPid; static volatile bool running = true;
  public static List<string> Dialogs = new List<string>();
  static dynamic G;
  public static bool TraceShapes = false;   // superseded by isolated raster capture + vectorizing
  class Job { public object Obj; public Dictionary<string, object> D; public List<object> Anc; public string Kind; public float[] Box; }
  static List<Job> Jobs = new List<Job>();

  // ------------------------------------------------------------------ polyline / polygon vertex recovery
  // The automation API exposes no vertices. We hit-test inside the bounding box with
  // GetVisibleObjectFromPosition(x, y, GoIntoSymbol=true): points that belong to the object
  // give its shape (convex hull for filled shapes; diagonal test for straight lines).
  static bool Hit(IntPtr me, double x, double y) {
    object r = null;
    try { r = G.GetVisibleObjectFromPosition((int)Math.Round(x), (int)Math.Round(y), true); } catch { }
    if (r == null) return false;
    IntPtr u = Marshal.GetIUnknownForObject(r);
    bool same = u == me;
    Marshal.Release(u);
    return same;
  }
  static List<double[]> Trace(object o, float l, float t, float w, float h, bool filled, Dictionary<string, object> info) {
    IntPtr me = Marshal.GetIUnknownForObject(o);
    try {
      if (w <= 3 || h <= 3) return null;           // horizontal / vertical line: bounding box is exact
      if (!filled) {
        int a = 0, b = 0;
        for (int k = 1; k <= 7; k++) { double f = k / 8.0; if (Hit(me, l + w * f, t + h * f)) a++; if (Hit(me, l + w * f, t + h * (1 - f))) b++; }
        if (a >= 5 && a >= b) { info["trace"] = "line"; return new List<double[]> { new double[] { l, t }, new double[] { l + w, t + h } }; }
        if (b >= 5) { info["trace"] = "line"; return new List<double[]> { new double[] { l, t + h }, new double[] { l + w, t } }; }
      }
      int n = (int)Math.Min(64, Math.Max(16, Math.Max(w, h) / 3));
      var pts = new List<double[]>();
      for (int iy = 0; iy <= n; iy++) for (int ix = 0; ix <= n; ix++) {
        double x = l + w * ix / n, y = t + h * iy / n;
        if (Hit(me, x, y)) pts.Add(new double[] { x, y });
      }
      info["samples"] = pts.Count;
      if (pts.Count < 3) return null;
      var hull = Hull(pts);
      info["trace"] = filled ? "hull" : "hull(open)";
      return hull;
    } finally { Marshal.Release(me); }
  }
  static double Cross(double[] o, double[] a, double[] b) { return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]); }
  static List<double[]> Hull(List<double[]> p) {      // Andrew monotone chain, collinear points removed
    p = p.OrderBy(q => q[0]).ThenBy(q => q[1]).ToList();
    var h = new List<double[]>();
    foreach (var q in p) { while (h.Count >= 2 && Cross(h[h.Count - 2], h[h.Count - 1], q) <= 0) h.RemoveAt(h.Count - 1); h.Add(q); }
    int lower = h.Count + 1;
    for (int i = p.Count - 2; i >= 0; i--) { var q = p[i]; while (h.Count >= lower && Cross(h[h.Count - 2], h[h.Count - 1], q) <= 0) h.RemoveAt(h.Count - 1); h.Add(q); }
    h.RemoveAt(h.Count - 1);
    return h;
  }
  public static int Step = 6;
  public static long PeelBudgetMs = 120000;

  static void Watchdog() {
    while (running) {
      try {
        EnumWindows((h, l) => {
          uint pid; GetWindowThreadProcessId(h, out pid);
          if (pid != gwxPid || !IsWindowVisible(h)) return true;
          var cls = new StringBuilder(64); GetClassName(h, cls, 64);
          if (cls.ToString() != "#32770") return true;
          var texts = new List<string>(); var buttons = new List<KeyValuePair<IntPtr,string>>();
          EnumChildWindows(h, (c, m) => { var t = new StringBuilder(1024); GetWindowText(c, t, 1024); var cc = new StringBuilder(64); GetClassName(c, cc, 64);
            if (cc.ToString() == "Button") buttons.Add(new KeyValuePair<IntPtr,string>(c, t.ToString().Replace("&", ""))); else if (t.Length > 0) texts.Add(t.ToString()); return true; }, IntPtr.Zero);
          string msg = String.Join(" ", texts);
          // language independent: standard dialog button ids IDNO=7 (don't save), IDOK=1, IDCLOSE=8, IDCANCEL=2
          IntPtr pick = IntPtr.Zero;
          foreach (int want in new[] { 7, 1, 8, 2 }) { foreach (var b in buttons) if (GetDlgCtrlID(b.Key) == want) { pick = b.Key; break; } if (pick != IntPtr.Zero) break; }
          lock (Dialogs) Dialogs.Add(msg + " -> " + (pick == IntPtr.Zero ? "WM_CLOSE" : "clicked"));
          if (pick != IntPtr.Zero) SendMessage(pick, BM_CLICK, IntPtr.Zero, IntPtr.Zero); else PostMessage(h, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
          return true; }, IntPtr.Zero);
      } catch { }
      Thread.Sleep(150);
    }
  }

  // ------------------------------------------------------------------ generic property dump (late bound)
  static readonly Dictionary<string, List<string>> propCache = new Dictionary<string, List<string>>();
  static string IfaceName(object o) {
    try { CT.ITypeInfo ti; int hr = ((IDispatchInfo)o).GetTypeInfo(0, 0x0800, out ti); if (hr != 0 || ti == null) return "?"; string n, d, f; int c; ti.GetDocumentation(-1, out n, out d, out c, out f); return n; } catch { return "?"; }
  }
  static readonly string[] KnownProps = {
    "objectName","fillColor","lineColor","shadowColor","isFilled","hasShadow","lineWidth","lineStyle","edgeStyle","Angle","Visible","Keyword","UserCustomData","UserDescription",
    "text","stretchText","alignment","BoundaryAlignment","Font","rounded","RoundingX","RoundingY","arcType","startAngle","endAngle",
    "dataSource","RangeOverride","highRange","lowRange","TimerRate","RequestDataType","EnableLanguageAlias","UseKeypad","ConfirmBeforeWrite",
    "ChangeColorOnTrue","UseFillColor","UseLineColor","UseShadowColor","AlternateStateHide","FlashWhenDataSourceTrue","ShowAltStateWhenNotFlashing",
    "pickAction","NameParameter","aliasCommandType","aliasCommandString","tracking","ScriptType","ScriptText",
    // properties of newer GraphWorX versions (tried, silently skipped when absent)
    "Format","DataEntry","HideWhenTrue","DisableObject","Clockwise","PivotX","PivotY","OffsetX","OffsetY","Slider","SizeType","StartSize","EndSize","Clip",
    "StartFillColor","EndFillColor","StartLineColor","EndLineColor","ChangeFill","ChangeLine","DefaultColorAbove","DefaultColorBelow","FileName","Modal","Center",
    "Value1","Value2","Value3","ExecutionTrigger","MouseButton","PickType","DataType","Update","InitialValue","HasInitialValue","TimeFormat","DateFormat","FormatType" };
  static List<string> PropNames(object o) {
    string iface = IfaceName(o);
    if (iface == "?") return KnownProps.ToList();
    List<string> names;
    if (propCache.TryGetValue(iface, out names)) return names;
    names = new List<string>();
    try {
      CT.ITypeInfo ti; ((IDispatchInfo)o).GetTypeInfo(0, 0, out ti);
      IntPtr pa; ti.GetTypeAttr(out pa); var ta = (CT.TYPEATTR)Marshal.PtrToStructure(pa, typeof(CT.TYPEATTR));
      for (int i = 0; i < ta.cFuncs; i++) {
        IntPtr pf; ti.GetFuncDesc(i, out pf); var fd = (CT.FUNCDESC)Marshal.PtrToStructure(pf, typeof(CT.FUNCDESC));
        if (fd.invkind == CT.INVOKEKIND.INVOKE_PROPERTYGET && fd.cParams == 0) { var nm = new string[1]; int cn; ti.GetNames(fd.memid, nm, 1, out cn); if (!names.Contains(nm[0])) names.Add(nm[0]); }
        ti.ReleaseFuncDesc(pf);
      }
      for (int i = 0; i < ta.cVars; i++) { IntPtr pv; ti.GetVarDesc(i, out pv); var vd = (CT.VARDESC)Marshal.PtrToStructure(pv, typeof(CT.VARDESC)); var nm = new string[1]; int cn; ti.GetNames(vd.memid, nm, 1, out cn); if (!names.Contains(nm[0])) names.Add(nm[0]); ti.ReleaseVarDesc(pv); }
      ti.ReleaseTypeAttr(pa);
    } catch { }
    propCache[iface] = names;
    return names;
  }
  static object GetP(object o, string name) { return o.GetType().InvokeMember(name, BindingFlags.GetProperty, null, o, null); }
  static object Call(object o, string name, params object[] a) { return o.GetType().InvokeMember(name, BindingFlags.InvokeMethod, null, o, a); }

  static object Val(object v) {
    if (v == null) return null;
    if (v is string || v is bool || v is int || v is short || v is long || v is double || v is float || v is uint || v is byte || v is ushort) return v;
    if (v is Array) { var l = new List<object>(); foreach (var x in (Array)v) l.Add(Val(x)); return l; }
    if (Marshal.IsComObject(v)) return null;
    return v.ToString();
  }

  static Dictionary<string, object> Props(object o) {
    var d = new Dictionary<string, object>();
    foreach (var p in PropNames(o)) {
      try {
        object v = GetP(o, p);
        if (p == "Font" && v != null) {
          d["Font"] = new Dictionary<string, object> { {"Name", GetP(v, "Name")}, {"Size", Convert.ToDouble(GetP(v, "Size"))},
            {"Bold", GetP(v, "Bold")}, {"Italic", GetP(v, "Italic")}, {"Underline", GetP(v, "Underline")} };
        } else d[p] = Val(v);
      } catch (Exception) { }
    }
    return d;
  }

  static Dictionary<string, object> DumpDynamic(object dy, int depth) {
    var d = Props(dy);
    d["_iface"] = IfaceName(dy);
    try { d["_type"] = (string)Call(dy, "GetObjectTypeName"); } catch { }
    if (depth < 3) {
      try {
        int n = Convert.ToInt32(Call(dy, "GetNumberOfSubDynamics"));
        var subs = new List<object>();
        for (int i = 0; i < n; i++) { object s = Call(dy, "GetSubDynamicObjectFromIndex", i); if (s != null) subs.Add(DumpDynamic(s, depth + 1)); }
        if (subs.Count > 0) d["_subs"] = subs;
      } catch { }
    }
    return d;
  }

  static Dictionary<string, object> DumpVisible(dynamic o, int depth, List<object> anc = null) {
    anc = anc ?? new List<object>();
    var d = Props((object)o);
    string typeName = o.GetObjectTypeName();
    d["_type"] = typeName; d["_iface"] = IfaceName((object)o);
    float l = 0, tp = 0, w = 0, h = 0; o.GetObjectDimensions(ref l, ref tp, ref w, ref h);
    d["_dims"] = new object[] { l, tp, w, h };
    bool vis = true; try { vis = (bool)o.Visible; } catch { }
    if (vis && w >= 1 && h >= 1) {
      string kind = null;
      if (typeName == "GwxLine") kind = "shape";                                   // vertices -> vectorize the isolated render
      else if (typeName == "GwxBitmap" || typeName == "GwxMetafile" || typeName == "GwxOleObject") kind = "raster";
      else if ((typeName == "GwxRectangle" || typeName == "GwxEllipse" || typeName == "GwxArc") && w >= 16 && h >= 16) {
        bool filled = false; try { filled = (bool)o.isFilled; } catch { }
        if (filled) kind = "texture?";                                             // decided after the reference capture
      }
      if (kind != null) Jobs.Add(new Job { Obj = (object)o, D = d, Anc = new List<object>(anc), Kind = kind, Box = new float[] { l, tp, w, h } });
    }
    if (TraceShapes && typeName == "GwxLine" && G != null) {
      var info = new Dictionary<string, object>();
      bool filled = false; try { filled = (bool)o.isFilled; } catch { }
      var pts = Trace((object)o, l, tp, w, h, filled, info);
      if (pts != null) { d["_points"] = pts.Select(q => new object[] { Math.Round(q[0], 1), Math.Round(q[1], 1) }).ToList(); d["_trace"] = info; }
    }
    var dyns = new List<object>();
    int nd = o.GetNumberOfDynamics();
    for (int i = 0; i < nd; i++) dyns.Add(DumpDynamic((object)o.GetDynamicObjectFromIndex(i), 0));
    if (dyns.Count > 0) d["_dyn"] = dyns;
    if (depth < 30 && typeName.IndexOf("Symbol", StringComparison.OrdinalIgnoreCase) >= 0) {
      int n = -1;
      try { n = o.GetNumberOfChildVisibles(); } catch { }
      if (n >= 0) {
        var kids = new List<object>();
        var anc2 = new List<object>(anc); anc2.Add((object)o);
        for (int i = 0; i < n; i++) kids.Add(DumpVisible(o.GetVisibleObjectFromIndex(i), depth + 1, anc2));
        d["_children"] = kids;
      }
    }
    return d;
  }

  // a "visual" dynamic changes the look at runtime (everything except pure click actions)
  static bool OwnVisualDynamic(dynamic o) {
    int n = o.GetNumberOfDynamics();
    for (int i = 0; i < n; i++) { string t = ""; try { t = (string)Call((object)o.GetDynamicObjectFromIndex(i), "GetObjectTypeName"); } catch { } if (t != "GwxPick") return true; }
    return false;
  }
  // hide (recursively) every object with a visual dynamic -> the capture contains all static artwork
  static void HideVisualDynamics(dynamic o, List<object> hidden) {
    if (OwnVisualDynamic(o)) { try { o.Visible = false; hidden.Add((object)o); } catch { } return; }
    int n = -1; try { n = o.GetNumberOfChildVisibles(); } catch { }
    for (int i = 0; i < n; i++) HideVisualDynamics(o.GetVisibleObjectFromIndex(i), hidden);
  }

  // ------------------------------------------------------------------ capture
  static bool OneToOne = false;
  static void PrepareView(dynamic g, int W, int H) {
    // try to show the display at 100 % so captures are pixel exact (no resampling)
    OneToOne = false;
    try {
      g.SetWindowDimensionsPixels(0, 0, W + 80, H + 160);
      g.ViewZoomPercent((float)100); g.ViewHome();
      int x0 = 0, y0 = 0, x1 = W, y1 = H; g.LogicalPointToClient(ref x0, ref y0); g.LogicalPointToClient(ref x1, ref y1);
      int cl = 0, ct = 0, cw = 0, ch = 0; g.GetClientDimensionsPixels(ref cl, ref ct, ref cw, ref ch);
      OneToOne = (x1 - x0) == W && (y1 - y0) == H && x0 >= 0 && y0 >= 0 && x1 <= cw && y1 <= ch;
    } catch { OneToOne = false; }
    if (!OneToOne) { try { g.ViewShowWholeDisplay(); } catch { } }
  }
  static Bitmap Capture(dynamic g, string tmpBmp, int W, int H) {
    if (File.Exists(tmpBmp)) File.Delete(tmpBmp);
    if (!OneToOne) { try { g.ViewShowWholeDisplay(); } catch { } }
    bool okb = g.ExportBitmapCaptureOfView(tmpBmp);
    if (!okb || !File.Exists(tmpBmp)) return null;
    using (var src = new Bitmap(tmpBmp)) {
      int x0 = 0, y0 = 0, x1 = W, y1 = H;
      try { g.LogicalPointToClient(ref x0, ref y0); g.LogicalPointToClient(ref x1, ref y1); } catch { x0 = 0; y0 = 0; x1 = src.Width; y1 = src.Height; }
      var r = Rectangle.Intersect(new Rectangle(x0, y0, Math.Max(1, x1 - x0), Math.Max(1, y1 - y0)), new Rectangle(0, 0, src.Width, src.Height));
      if (r.Width < 2 || r.Height < 2) r = new Rectangle(0, 0, src.Width, src.Height);
      var dst = new Bitmap(W, H);
      using (var gr = Graphics.FromImage(dst)) {
        bool exact = r.Width == W && r.Height == H;
        gr.InterpolationMode = exact ? System.Drawing.Drawing2D.InterpolationMode.NearestNeighbor : System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
        gr.PixelOffsetMode = System.Drawing.Drawing2D.PixelOffsetMode.Half;
        gr.DrawImage(src, new Rectangle(0, 0, W, H), r, GraphicsUnit.Pixel);
      }
      return dst;
    }
  }

  // ------------------------------------------------------------------ isolated object capture
  static bool Same(object a, object b) {
    IntPtr ua = Marshal.GetIUnknownForObject(a), ub = Marshal.GetIUnknownForObject(b);
    bool r = ua == ub; Marshal.Release(ua); Marshal.Release(ub); return r;
  }
  static void HideExcept(IEnumerable<object> list, object keep, List<object> hidden) {
    foreach (var x in list) {
      if (keep != null && Same(x, keep)) continue;
      try { if ((bool)((dynamic)x).Visible) { ((dynamic)x).Visible = false; hidden.Add(x); } } catch { }
    }
  }
  static IEnumerable<object> Children(object sym) {
    var res = new List<object>(); int n = -1;
    try { n = ((dynamic)sym).GetNumberOfChildVisibles(); } catch { }
    for (int i = 0; i < n; i++) res.Add((object)((dynamic)sym).GetVisibleObjectFromIndex(i));
    return res;
  }
  // render ONE object alone on a magenta key background -> PNG with transparency (no other artwork)
  static string Isolate(dynamic g, Job j, List<object> tops, string outDir, string name, int k, string tmp, int W, int H) {
    var hidden = new List<object>();
    long bgOld = 0; try { bgOld = Convert.ToInt64(g.BackgroundColor); } catch { }
    try {
      HideExcept(tops, j.Anc.Count > 0 ? j.Anc[0] : j.Obj, hidden);
      for (int i = 0; i < j.Anc.Count; i++) HideExcept(Children(j.Anc[i]), i + 1 < j.Anc.Count ? j.Anc[i + 1] : j.Obj, hidden);
      // two renders on different key colours -> exact alpha for anti-aliased edges and soft parts
      try { g.BackgroundColor = (uint)0x00FF00FF; } catch { }          // magenta (COLORREF 0x00BBGGRR)
      Bitmap fa = Capture(g, tmp, W, H);
      try { g.BackgroundColor = (uint)0x0000FF00; } catch { }          // green
      Bitmap fb = Capture(g, tmp, W, H);
      if (fa == null || fb == null) { if (fa != null) fa.Dispose(); if (fb != null) fb.Dispose(); return null; }
      using (fa) using (fb) {
        int x0 = Math.Max(0, (int)Math.Floor(j.Box[0]) - 2), y0 = Math.Max(0, (int)Math.Floor(j.Box[1]) - 2);
        int x1 = Math.Min(W, (int)Math.Ceiling(j.Box[0] + j.Box[2]) + 3), y1 = Math.Min(H, (int)Math.Ceiling(j.Box[1] + j.Box[3]) + 3);
        if (x1 - x0 < 1 || y1 - y0 < 1) return null;
        var crop = new Bitmap(x1 - x0, y1 - y0, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
        int painted = 0;
        for (int y = 0; y < crop.Height; y++) for (int x = 0; x < crop.Width; x++) {
          var a = fa.GetPixel(x0 + x, y0 + y); var b = fb.GetPixel(x0 + x, y0 + y);
          // keys (255,0,255) and (0,255,0) differ by 255 in every channel -> alpha = 1 - mean difference
          double al = 1 - (Math.Abs(a.R - b.R) + Math.Abs(a.G - b.G) + Math.Abs(a.B - b.B)) / (3.0 * 255);
          if (al < 0.04) { crop.SetPixel(x, y, Color.Transparent); continue; }
          if (al > 1) al = 1;
          painted++;
          int r = (int)Math.Max(0, Math.Min(255, (a.R - (1 - al) * 255) / al));
          int gg = (int)Math.Max(0, Math.Min(255, a.G / al));
          int bl = (int)Math.Max(0, Math.Min(255, (a.B - (1 - al) * 255) / al));
          crop.SetPixel(x, y, Color.FromArgb((int)Math.Round(al * 255), r, gg, bl));
        }
        string file = name + "_o" + k + ".png";
        crop.Save(Path.Combine(outDir, file), System.Drawing.Imaging.ImageFormat.Png);
        crop.Dispose();
        j.D["_rbox"] = new object[] { x0, y0, x1 - x0, y1 - y0 };
        j.D["_painted"] = painted;
        return file;
      }
    } finally {
      foreach (var x in hidden) { try { ((dynamic)x).Visible = true; } catch { } }
      try { g.BackgroundColor = (uint)bgOld; } catch { }
    }
  }
  // a filled shape whose area in the full rendering is not one flat colour -> pattern / bitmap brush
  static bool Textured(Bitmap refBmp, float[] b) {
    if (refBmp == null) return false;
    var lum = new List<double>();
    for (int iy = 1; iy < 16; iy++) for (int ix = 1; ix < 16; ix++) {
      int x = (int)(b[0] + 3 + (b[2] - 6) * ix / 16), y = (int)(b[1] + 3 + (b[3] - 6) * iy / 16);
      if (x < 0 || y < 0 || x >= refBmp.Width || y >= refBmp.Height) continue;
      var c = refBmp.GetPixel(x, y); lum.Add(0.3 * c.R + 0.59 * c.G + 0.11 * c.B);
    }
    if (lum.Count < 20) return false;
    double m = lum.Average(), sd = Math.Sqrt(lum.Select(v => (v - m) * (v - m)).Average());
    if (sd < 10) return false;
    int top = lum.GroupBy(v => (int)v / 12).Max(gp => gp.Count());
    return (double)top / lum.Count <= 0.7;
  }

  // GetHeadObject() is null at display level in GraphWorX32, so top level objects are
  // found by hit testing a grid, then "peeling" (hiding found objects and re-testing
  // their area) to reach covered objects. The peel depth gives the z-order.
  public static List<object> Enumerate(dynamic g, int W, int H, int step, Dictionary<string, object> stats) {
    var byPtr = new Dictionary<IntPtr, int>();
    var found = new List<object>(); var layer = new List<int>(); var boxes = new List<float[]>();
    var pts = new List<List<int[]>>();                 // grid points where each object was the topmost hit
    var below = new List<HashSet<int>>();              // below[a] = objects directly under a
    var queue = new Queue<int>();
    int tests = 0;
    Func<int, int, int, int> probe = (x, y, lay) => {
      tests++;
      object o = null; try { o = g.GetVisibleObjectFromPosition(x, y, false); } catch { }
      if (o == null) return -1;
      IntPtr u = Marshal.GetIUnknownForObject(o);
      int idx;
      if (!byPtr.TryGetValue(u, out idx)) {
        idx = found.Count; byPtr[u] = idx; found.Add(o); layer.Add(lay); pts.Add(new List<int[]>()); below.Add(new HashSet<int>());
        float l = 0, t = 0, w = 0, h = 0; try { ((dynamic)o).GetObjectDimensions(ref l, ref t, ref w, ref h); } catch { }
        boxes.Add(new float[] { l, t, w, h }); queue.Enqueue(idx);
      } else Marshal.Release(u);
      if (pts[idx].Count < 400) pts[idx].Add(new[] { x, y });
      return idx;
    };
    for (int y = step / 2; y < H; y += step) for (int x = step / 2; x < W; x += step) probe(x, y, 0);
    var hidden = new List<object>();
    int guard = 0;
    var budget = Stopwatch.StartNew();
    while (queue.Count > 0 && guard++ < 5000) {
      if (budget.ElapsedMilliseconds > PeelBudgetMs) { stats["peel_truncated"] = true; break; }
      int i = queue.Dequeue();
      var o = found[i];
      try { ((dynamic)o).Visible = false; hidden.Add(o); } catch { continue; }
      // exactly where i was on top: whatever is there now lies directly below i
      foreach (var p in pts[i].ToList()) { int k = probe(p[0], p[1], layer[i] + 1); if (k >= 0 && k != i) below[i].Add(k); }
      var b = boxes[i];
      int x0 = (int)Math.Max(0, b[0]), y0 = (int)Math.Max(0, b[1]), x1 = (int)Math.Min(W, b[0] + b[2] + 1), y1 = (int)Math.Min(H, b[1] + b[3] + 1);
      for (int y = y0 + step / 2; y < y1; y += step) for (int x = x0 + step / 2; x < x1; x += step) probe(x, y, layer[i] + 1);
    }
    foreach (var o in hidden) { try { ((dynamic)o).Visible = true; } catch { } }
    stats["hit_tests"] = tests; stats["peel_layers"] = layer.Count == 0 ? 0 : layer.Max() + 1;
    // order bottom -> top: an object is emitted only after everything below it (Kahn), ties by depth then discovery
    int n = found.Count;
    var indeg = new int[n];                                    // number of objects that must come before (below it)
    var above = new List<int>[n]; for (int a = 0; a < n; a++) above[a] = new List<int>();
    for (int a = 0; a < n; a++) foreach (int bb in below[a]) { indeg[a]++; above[bb].Add(a); }
    var done = new bool[n]; var order = new List<int>();
    while (order.Count < n) {
      int pick = -1;
      for (int a = 0; a < n; a++) if (!done[a] && indeg[a] == 0 && (pick < 0 || layer[a] > layer[pick] || (layer[a] == layer[pick] && a < pick))) pick = a;
      if (pick < 0) { for (int a = 0; a < n; a++) if (!done[a] && (pick < 0 || indeg[a] < indeg[pick])) pick = a; }   // cycle: break it
      done[pick] = true; order.Add(pick);
      foreach (int a in above[pick]) indeg[a]--;
    }
    stats["occlusion_edges"] = below.Sum(x => x.Count);
    return order.Select(k => found[k]).ToList();
  }

  public static string Run(string[] files, string outDir, bool snapshots, bool bake) {
    var js = new JavaScriptSerializer(); js.MaxJsonLength = int.MaxValue; js.RecursionLimit = 1000;
    Directory.CreateDirectory(outDir);
    var before = new HashSet<int>(Process.GetProcessesByName("Gwx32").Select(p => p.Id));
    dynamic g = Activator.CreateInstance(Type.GetTypeFromProgID("Gwx32.Display"));
    var mine = Process.GetProcessesByName("Gwx32").Where(p => !before.Contains(p.Id)).ToList();
    if (mine.Count != 1) {
      // COM attached to an already running GraphWorX (yours) - never touch it
      try { System.Runtime.InteropServices.Marshal.FinalReleaseComObject((object)g); } catch { }
      return new JavaScriptSerializer().Serialize(new[] { new Dictionary<string, object> { {"ok", false}, {"error", "GraphWorX did not start a private instance (attached to a running one) - aborted without touching it. Close GraphWorX and retry."} } });
    }
    gwxPid = (uint)mine[0].Id;
    G = g;
    var wd = new Thread(Watchdog); wd.IsBackground = true; wd.Start();
    var summary = new List<object>();
    try {
      try { g.SetWindowDimensionsPixels(0, 0, 2200, 1500); } catch { }
      foreach (var file in files) {
        var rep = new Dictionary<string, object> { {"file", file} };
        try {
          string name = Path.GetFileNameWithoutExtension(file);
          lock (Dialogs) Dialogs.Clear();
          var sw = Stopwatch.StartNew();
          bool opened = (bool)g.FileOpen(file);
          rep["open_ms"] = sw.ElapsedMilliseconds;
          if (!opened) throw new Exception("FileOpen failed");
          int W = 0, H = 0; g.GetDisplayDimensions(ref W, ref H);
          PrepareView(g, W, H);
          rep["capture_1to1"] = OneToOne;
          dynamic head = null; try { head = g.GetHeadObject(); } catch { }
          var objs = new List<object>();
          var tops = new List<object>();
          var est = new Dictionary<string, object>();
          if (head != null) { int n = head.GetNumberOfChildVisibles(); for (int i = 0; i < n; i++) tops.Add((object)head.GetVisibleObjectFromIndex(i)); est["method"] = "head"; }
          else { var sw2 = Stopwatch.StartNew(); tops = Enumerate(g, W, H, Step, est); est["method"] = "hit-test peel"; est["ms"] = sw2.ElapsedMilliseconds; }
          Jobs.Clear();
          foreach (var o in tops) objs.Add(DumpVisible((dynamic)o, 0));
          rep["enumeration"] = est;
          long bg = 0; try { bg = Convert.ToInt64(g.BackgroundColor); } catch { }
          var doc = new Dictionary<string, object> { {"file", file}, {"width", W}, {"height", H}, {"background", bg}, {"objects", objs} };
          try { doc["displayScript"] = (string)g.GetEventScriptText(6); } catch { }
          {
            string tmp = Path.Combine(outDir, name + ".tmp.bmp");
            // reference rendering: used for the visual identity test and pattern detection, never imported
            Bitmap refBmp = Capture(g, tmp, W, H);
            if (refBmp != null) { refBmp.Save(Path.Combine(outDir, name + ".ref.png"), System.Drawing.Imaging.ImageFormat.Png); doc["ref"] = name + ".ref.png"; }
            int k = 0, nIso = 0; var swIso = Stopwatch.StartNew();
            foreach (var j in Jobs) {
              if (j.Kind == "texture?") { if (!Textured(refBmp, j.Box)) continue; j.Kind = "texture"; }
              string f = null;
              try { f = Isolate(g, j, tops, outDir, name, ++k, tmp, W, H); } catch (Exception e) { j.D["_raster_error"] = e.Message; }
              if (f != null) { j.D["_raster"] = f; j.D["_raster_kind"] = j.Kind; nIso++; }
            }
            rep["isolated_captures"] = nIso; rep["isolated_ms"] = swIso.ElapsedMilliseconds;
            if (refBmp != null) refBmp.Dispose();
            if (bake) {
              // hide every object with a visual dynamic (any depth) -> capture static artwork only
              var hidden = new List<object>();
              foreach (var o in tops) HideVisualDynamics((dynamic)o, hidden);
              var b = Capture(g, tmp, W, H);
              foreach (var o in hidden) { try { ((dynamic)o).Visible = true; } catch { } }
              if (b != null) { b.Save(Path.Combine(outDir, name + ".bg.png"), System.Drawing.Imaging.ImageFormat.Png); b.Dispose(); doc["bg"] = name + ".bg.png"; doc["bakedHidden"] = hidden.Count; }
            }
            if (File.Exists(tmp)) File.Delete(tmp);
          }
          lock (Dialogs) doc["dialogs"] = Dialogs.ToList();
          File.WriteAllText(Path.Combine(outDir, name + ".json"), js.Serialize(doc), new UTF8Encoding(false));
          rep["ok"] = true; rep["json"] = name + ".json"; rep["objects"] = objs.Count;
        } catch (Exception e) { rep["ok"] = false; rep["error"] = e.Message; lock (Dialogs) rep["dialogs"] = Dialogs.ToList(); }
        rep["pid"] = gwxPid;
        summary.Add(rep);
      }
    } finally {
      try { g.ExitApplication(); } catch { }
      Thread.Sleep(800);
      running = false;
      if (gwxPid != 0) { try { var p = Process.GetProcessById((int)gwxPid); if (!p.HasExited) p.Kill(); } catch { } }
    }
    return js.Serialize(summary);
  }
}
"@
$list = $Files -split '\|' | Where-Object { $_ }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[GwxExport]::Run($list, $Out, $Snapshots.IsPresent, $Bake.IsPresent)
