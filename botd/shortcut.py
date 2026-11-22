"""Windows shortcuts ("Free Discord Bot") with the app logo, on the Start menu and (from setup.cmd) the desktop.

The shortcut carries the same app id as the window (APP_ID), so Windows shows the app's own logo in the
taskbar and pinning the running window pins this shortcut, not Python.

Run by hand:  .venv\\Scripts\\python.exe -m botd.shortcut
"""
import os
import subprocess
import sys
from pathlib import Path

APP_ID = "FreeDiscordBot.Manager"
NAME = "Free Discord Bot"
ROOT = Path(__file__).resolve().parents[1]

# Creates the .lnk and stamps the app id on it (WScript.Shell alone cannot set the app id).
_PS = r'''
$ErrorActionPreference = "Stop"
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($env:FDB_LNK)
$lnk.TargetPath = $env:FDB_TARGET
$lnk.Arguments = '"' + $env:FDB_SCRIPT + '"'
$lnk.WorkingDirectory = $env:FDB_DIR
$lnk.IconLocation = $env:FDB_ICON + ",0"
$lnk.Description = "Free Discord Bot manager"
$lnk.Save()
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)] public struct PropVar {
  [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr pad; }
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PropKey k);
  void GetValue(ref PropKey k, out PropVar v); void SetValue(ref PropKey k, ref PropVar v); void Commit(); }
[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPersistFile { void GetClassID(out Guid g); [PreserveSig] int IsDirty();
  void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint m); void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool r);
  void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f); void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f); }
public static class FdbAppId {
  public static void Set(string path, string id) {
    var link = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
    ((IPersistFile)link).Load(path, 2);
    var key = new PropKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    var v = new PropVar { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };
    ((IPropertyStore)link).SetValue(ref key, ref v);
    ((IPropertyStore)link).Commit();
    ((IPersistFile)link).Save(path, true);
    Marshal.FreeCoTaskMem(v.p);
  }
}
"@
[FdbAppId]::Set($env:FDB_LNK, $env:FDB_APPID)
'''


def _folders(desktop: bool) -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    out = [appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"]
    if desktop:
        out.append(Path(os.environ.get("USERPROFILE", "")) / "Desktop")
    return [f for f in out if f.is_dir()]


def create_shortcuts(desktop: bool = True, only_if_missing: bool = False) -> list[Path]:
    if os.name != "nt":
        return []
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    target = pythonw if pythonw.exists() else Path(sys.executable).with_name("pythonw.exe")
    made = []
    for folder in _folders(desktop):
        lnk = folder / f"{NAME}.lnk"
        if only_if_missing and lnk.exists():
            continue
        env = dict(os.environ, FDB_LNK=str(lnk), FDB_TARGET=str(target), FDB_SCRIPT=str(ROOT / "app.py"),
                   FDB_DIR=str(ROOT), FDB_ICON=str(ROOT / "ui" / "logo.ico"), FDB_APPID=APP_ID)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS],
                       env=env, check=True, capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        made.append(lnk)
    return made


if __name__ == "__main__":
    for path in create_shortcuts(desktop="--no-desktop" not in sys.argv):
        print("shortcut created:", path)
