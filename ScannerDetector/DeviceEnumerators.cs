using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace ScannerDetector
{
    internal static class WiaEnumerator
    {
        private const int ScannerDeviceType = 1;

        public static List<string> Enumerate()
        {
            var result = new List<string>();
            var managerType = Type.GetTypeFromProgID("WIA.DeviceManager", true);
            dynamic manager = Activator.CreateInstance(managerType);
            try
            {
                dynamic infos = manager.DeviceInfos;
                int count = (int)infos.Count;
                for (int i = 1; i <= count; i++)
                {
                    dynamic info = infos[i];
                    try
                    {
                        if ((int)info.Type != ScannerDeviceType) continue;
                        string name = ReadWiaProperty(info.Properties, "Name");
                        string id = ReadWiaProperty(info.Properties, "DeviceID");
                        result.Add(string.IsNullOrWhiteSpace(name) ? id : name + (string.IsNullOrWhiteSpace(id) ? "" : "  [" + id + "]"));
                    }
                    finally { Release(info); }
                }
                Release(infos);
            }
            finally
            {
                Release(manager);
            }
            return result;
        }

        private static string ReadWiaProperty(dynamic properties, string wanted)
        {
            for (int i = 1; i <= (int)properties.Count; i++)
            {
                dynamic property = properties[i];
                try
                {
                    if (string.Equals((string)property.Name, wanted, StringComparison.OrdinalIgnoreCase))
                        return Convert.ToString(property.Value);
                }
                finally { Release(property); }
            }
            return "";
        }

        private static void Release(object value)
        {
            if (value != null && Marshal.IsComObject(value)) Marshal.ReleaseComObject(value);
        }
    }

    internal static class TwainEnumerator
    {
        private const ushort DgControl = 0x0001;
        private const ushort DatIdentity = 0x0003;
        private const ushort DatParent = 0x0004;
        private const ushort MsgOpenDsm = 0x0301;
        private const ushort MsgCloseDsm = 0x0302;
        private const ushort MsgGetFirst = 0x0401;
        private const ushort MsgGetNext = 0x0402;
        private const ushort TwrcSuccess = 0;
        private const ushort TwrcEndOfList = 3;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 2)]
        private struct TwVersion
        {
            public ushort Major;
            public ushort Minor;
            public ushort Language;
            public ushort Country;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 34)] public string Info;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi, Pack = 2)]
        private struct TwIdentity
        {
            public uint Id;
            public TwVersion Version;
            public ushort ProtocolMajor;
            public ushort ProtocolMinor;
            public uint SupportedGroups;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 34)] public string Manufacturer;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 34)] public string ProductFamily;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 34)] public string ProductName;
        }

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate ushort DsmEntry(ref TwIdentity origin, IntPtr destination, ushort dg, ushort dat, ushort msg, IntPtr data);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr LoadLibrary(string fileName);
        [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
        private static extern IntPtr GetProcAddress(IntPtr module, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool FreeLibrary(IntPtr module);

        public static List<string> Enumerate()
        {
            if (Environment.Is64BitProcess) throw new InvalidOperationException("TWAIN 조회는 x86 프로세스에서 실행해야 합니다.");
            IntPtr module = LoadLibrary("TWAIN_32.DLL");
            if (module == IntPtr.Zero) throw new InvalidOperationException("TWAIN_32.DLL을 찾을 수 없습니다.");
            try
            {
                IntPtr proc = GetProcAddress(module, "DSM_Entry");
                if (proc == IntPtr.Zero) throw new InvalidOperationException("TWAIN DSM_Entry 함수를 찾을 수 없습니다.");
                var entry = (DsmEntry)Marshal.GetDelegateForFunctionPointer(proc, typeof(DsmEntry));
                var app = new TwIdentity
                {
                    Id = 0,
                    Version = new TwVersion { Major = 1, Minor = 0, Language = 13, Country = 1, Info = "Scanner Detector" },
                    ProtocolMajor = 2,
                    ProtocolMinor = 4,
                    SupportedGroups = 0x00000001 | 0x00000002,
                    Manufacturer = "VirtualScanner",
                    ProductFamily = "Diagnostic",
                    ProductName = "Scanner Detector"
                };

                // DAT_PARENT expects a pointer to the application's native parent window handle.
                IntPtr parent = Marshal.AllocHGlobal(IntPtr.Size);
                Marshal.WriteIntPtr(parent, IntPtr.Zero);
                try
                {
                    ushort open = entry(ref app, IntPtr.Zero, DgControl, DatParent, MsgOpenDsm, parent);
                    if (open != TwrcSuccess) throw new InvalidOperationException("TWAIN DSM 열기 실패 (return code " + open + ").");
                    var result = new List<string>();
                    try
                    {
                        IntPtr identityPointer = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(TwIdentity)));
                        try
                        {
                            var identity = new TwIdentity();
                            Marshal.StructureToPtr(identity, identityPointer, false);
                            ushort rc = entry(ref app, IntPtr.Zero, DgControl, DatIdentity, MsgGetFirst, identityPointer);
                            while (rc == TwrcSuccess)
                            {
                                identity = (TwIdentity)Marshal.PtrToStructure(identityPointer, typeof(TwIdentity));
                                result.Add(Clean(identity.ProductName) + "  [" + Clean(identity.Manufacturer) + "; " + Clean(identity.ProductFamily) + "]");
                                identity = new TwIdentity();
                                Marshal.StructureToPtr(identity, identityPointer, false);
                                rc = entry(ref app, IntPtr.Zero, DgControl, DatIdentity, MsgGetNext, identityPointer);
                            }
                            if (rc != TwrcEndOfList && rc != TwrcSuccess) throw new InvalidOperationException("TWAIN Source 열거 실패 (return code " + rc + ").");
                        }
                        finally { Marshal.FreeHGlobal(identityPointer); }
                    }
                    finally
                    {
                        ushort close = entry(ref app, IntPtr.Zero, DgControl, DatParent, MsgCloseDsm, parent);
                        if (close != TwrcSuccess) { /* keep the enumeration result; DSM may already have closed itself */ }
                    }
                    return result;
                }
                finally { Marshal.FreeHGlobal(parent); }
            }
            finally { FreeLibrary(module); }
        }

        private static string Clean(string value) { return (value ?? "").Trim('\0', ' '); }
    }
}
