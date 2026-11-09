#requires -Version 5.1
# Read the existing Unicode shell link; do not create a new WScript shortcut.
if (-not ('Chengxin.Tests.ShortcutTarget' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace Chengxin.Tests {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink { }

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellLinkW {
        [PreserveSig]
        int GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path,
                    int size, IntPtr findData, uint flags);
    }

    public static class ShortcutTarget {
        public static string Read(string path) {
            if (!File.Exists(path)) throw new FileNotFoundException("Shortcut missing", path);
            object link = new ShellLink();
            try {
                ((IPersistFile)link).Load(path, 0);
                var target = new StringBuilder(32768);
                int result = ((IShellLinkW)link).GetPath(target, target.Capacity, IntPtr.Zero, 0);
                Marshal.ThrowExceptionForHR(result);
                if (target.Length == 0) throw new InvalidOperationException("Shell link target is empty: " + path);
                return target.ToString();
            } finally {
                Marshal.FinalReleaseComObject(link);
            }
        }
    }
}
'@
}

function Get-ChengxinShortcutTarget {
    param([Parameter(Mandatory=$true)][string]$Path)
    return [Chengxin.Tests.ShortcutTarget]::Read($Path)
}
