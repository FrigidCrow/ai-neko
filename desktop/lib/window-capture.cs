using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace AiNeko.NativeCapture
{
    public sealed class SourceIdentity
    {
        public string id { get; set; }
        public string name { get; set; }
        public int pid { get; set; }
        public string started { get; set; }
    }

    // The only pixel acquisition entry is PrintWindow for this exact HWND.
    // All other Win32 calls inspect metadata; there is no alternate capture path.
    public static class WindowCapture
    {
        private const int MaxSide = 8192;
        private const long MaxPixels = 16000000;
        private const int MaxJpegBytes = 2 * 1024 * 1024;
        private const int MaxInputBytes = 2 * 1024 * 1024;
        private const int MaxOutputChars = 3 * 1024 * 1024;
        private static readonly Regex WindowId = new Regex(@"\Awindow:([1-9][0-9]{0,19}):[01]\z", RegexOptions.CultureInvariant);
        private static readonly Regex StartedTicks = new Regex(@"\A[1-9][0-9]{0,18}\z", RegexOptions.CultureInvariant);

        // This standalone entrypoint does not need a command interpreter or a
        // PowerShell execution-policy exception. Validation never calls Win32.
        public static int Main(string[] args)
        {
            string failure = "invalid_request";
            try
            {
                Console.OutputEncoding = new UTF8Encoding(false);
                if (args.Length == 1 && args[0] == "--validate-only")
                {
                    Console.Out.WriteLine("{\"validated\":true}");
                    return 0;
                }
                if (args.Length != 0) throw new InvalidOperationException("invalid_request");
                JavaScriptSerializer serializer = new JavaScriptSerializer();
                serializer.MaxJsonLength = MaxInputBytes;
                serializer.RecursionLimit = 8;
                Dictionary<string, object> request = serializer.DeserializeObject(ReadRequest()) as Dictionary<string, object>;
                if (request == null || !request.ContainsKey("operation") || !(request["operation"] is string))
                    throw new InvalidOperationException("invalid_request");
                string operation = (string)request["operation"];
                List<SourceIdentity> sources = new List<SourceIdentity>();
                if (operation == "identify")
                {
                    RequireKeys(request, "operation", "sources");
                    object[] items = request["sources"] as object[];
                    if (items == null || items.Length > 250) throw new InvalidOperationException("invalid_request");
                    // Validate every entry before querying even one HWND.
                    foreach (object item in items) sources.Add(ReadSource(item, false));
                }
                else if (operation == "capture")
                {
                    RequireKeys(request, "operation", "source");
                    sources.Add(ReadSource(request["source"], true));
                }
                else throw new InvalidOperationException("invalid_request");
                failure = "native_unavailable";
                if (Environment.OSVersion.Platform != PlatformID.Win32NT || IntPtr.Size != 8)
                    throw new InvalidOperationException("native_unavailable");
                // Keep GetWindowRect in physical pixels on every monitor. This
                // affects only this helper thread and is never reached by the
                // validation-only branch or malformed requests.
                IntPtr priorDpiContext = SetThreadDpiAwarenessContext(new IntPtr(-4));
                if (priorDpiContext == IntPtr.Zero) throw new InvalidOperationException("native_unavailable");
                Dictionary<string, object> response;
                try
                {
                    failure = "capture_failed";
                    if (operation == "identify")
                    {
                        List<SourceIdentity> identified = new List<SourceIdentity>();
                        foreach (SourceIdentity source in sources)
                        {
                            // A missing or inaccessible window is omitted; an
                            // unrelated window is never used as its replacement.
                            try { identified.Add(Identify(source.id, source.name)); }
                            catch { }
                        }
                        response = new Dictionary<string, object> { { "sources", identified } };
                    }
                    else
                    {
                        SourceIdentity source = sources[0];
                        string jpeg = Capture(source.id, source.name, source.pid, source.started);
                        response = new Dictionary<string, object> { { "jpeg", jpeg } };
                    }
                }
                finally
                {
                    if (SetThreadDpiAwarenessContext(priorDpiContext) == IntPtr.Zero)
                    {
                        failure = "native_unavailable";
                        throw new InvalidOperationException("native_unavailable");
                    }
                }
                JavaScriptSerializer outputSerializer = new JavaScriptSerializer();
                outputSerializer.MaxJsonLength = MaxOutputChars;
                outputSerializer.RecursionLimit = 8;
                Console.Out.WriteLine(outputSerializer.Serialize(response));
                return 0;
            }
            catch
            {
                // Do not echo titles, raw requests, native errors or paths.
                Console.Out.WriteLine("{\"error\":\"" + failure + "\"}");
                return 1;
            }
        }

        private static string ReadRequest()
        {
            using (Stream input = Console.OpenStandardInput())
            using (MemoryStream bytes = new MemoryStream())
            {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
                {
                    if (bytes.Length + count > MaxInputBytes) throw new InvalidOperationException("invalid_request");
                    bytes.Write(buffer, 0, count);
                }
                return new UTF8Encoding(false, true).GetString(bytes.ToArray());
            }
        }

        private static void RequireKeys(Dictionary<string, object> value, params string[] names)
        {
            if (value == null || value.Count != names.Length) throw new InvalidOperationException("invalid_request");
            foreach (string name in names)
                if (!value.ContainsKey(name)) throw new InvalidOperationException("invalid_request");
        }

        private static SourceIdentity ReadSource(object value, bool identity)
        {
            Dictionary<string, object> source = value as Dictionary<string, object>;
            if (identity) RequireKeys(source, "id", "name", "pid", "started");
            else RequireKeys(source, "id", "name");
            string id = source["id"] as string, name = source["name"] as string;
            if (id == null || name == null || name.Length > 4096 || name.IndexOf('\0') >= 0)
                throw new InvalidOperationException("invalid_request");
            ParseWindow(id);
            SourceIdentity result = new SourceIdentity { id = id, name = name };
            if (identity)
            {
                object pid = source["pid"];
                string started = source["started"] as string;
                long ticks;
                if (!(pid is int) && !(pid is long)) throw new InvalidOperationException("invalid_request");
                long processId = Convert.ToInt64(pid, System.Globalization.CultureInfo.InvariantCulture);
                if (processId < 1 || processId > Int32.MaxValue || started == null || !StartedTicks.IsMatch(started) ||
                    !Int64.TryParse(started, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out ticks) ||
                    ticks > DateTime.MaxValue.Ticks) throw new InvalidOperationException("invalid_request");
                result.pid = (int)processId;
                result.started = started;
            }
            return result;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct WindowRect { public int Left, Top, Right, Bottom; }

        [DllImport("user32.dll")]
        private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
        [DllImport("user32.dll")]
        private static extern IntPtr GetDesktopWindow();
        [DllImport("user32.dll")]
        private static extern IntPtr GetShellWindow();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextLength(IntPtr hwnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetWindowRect(IntPtr hwnd, out WindowRect rect);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool PrintWindow(IntPtr hwnd, IntPtr destination, uint flags);

        private static IntPtr ParseWindow(string id)
        {
            Match match = WindowId.Match(id ?? "");
            ulong value;
            if (!match.Success || !UInt64.TryParse(match.Groups[1].Value, out value) ||
                value == 0 || value > (ulong)Int64.MaxValue || (IntPtr.Size == 4 && value > UInt32.MaxValue))
                throw new InvalidOperationException("invalid_request");
            return IntPtr.Size == 8 ? new IntPtr(unchecked((long)value)) : new IntPtr(unchecked((int)value));
        }

        private static SourceIdentity Check(IntPtr hwnd, string id, string name, out WindowRect bounds)
        {
            if (name == null || name.Length > 4096 || name.IndexOf('\0') >= 0 ||
                !IsWindow(hwnd) || !IsWindowVisible(hwnd) || IsIconic(hwnd) ||
                GetAncestor(hwnd, 2) != hwnd || hwnd == GetDesktopWindow() || hwnd == GetShellWindow())
                throw new InvalidOperationException("capture_failed");
            int length = GetWindowTextLength(hwnd);
            if (length < 0 || length > 4096) throw new InvalidOperationException("capture_failed");
            StringBuilder title = new StringBuilder(4097);
            GetWindowText(hwnd, title, title.Capacity);
            if (!String.Equals(title.ToString(), name, StringComparison.Ordinal))
                throw new InvalidOperationException("capture_failed");
            uint processId;
            if (GetWindowThreadProcessId(hwnd, out processId) == 0 || processId == 0 || processId > Int32.MaxValue ||
                !GetWindowRect(hwnd, out bounds)) throw new InvalidOperationException("capture_failed");
            long width = (long)bounds.Right - bounds.Left, height = (long)bounds.Bottom - bounds.Top;
            if (width < 1 || height < 1 || width > MaxSide || height > MaxSide || width * height > MaxPixels)
                throw new InvalidOperationException("capture_failed");
            string started;
            using (Process process = Process.GetProcessById((int)processId))
            {
                if (process.HasExited) throw new InvalidOperationException("capture_failed");
                started = process.StartTime.ToUniversalTime().Ticks.ToString(System.Globalization.CultureInfo.InvariantCulture);
            }
            return new SourceIdentity { id = id, name = name, pid = (int)processId, started = started };
        }

        private static void RequireIdentity(SourceIdentity actual, int pid, string started)
        {
            if (pid < 1 || actual.pid != pid || !String.Equals(actual.started, started, StringComparison.Ordinal))
                throw new InvalidOperationException("capture_failed");
        }

        public static SourceIdentity Identify(string id, string name)
        {
            IntPtr hwnd = ParseWindow(id);
            WindowRect before, after;
            SourceIdentity first = Check(hwnd, id, name, out before);
            SourceIdentity second = Check(hwnd, id, name, out after);
            RequireIdentity(second, first.pid, first.started);
            return second;
        }

        private static bool HasVisiblePixel(Bitmap bitmap)
        {
            BitmapData data = bitmap.LockBits(new Rectangle(0, 0, bitmap.Width, bitmap.Height),
                ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
            try
            {
                byte[] row = new byte[checked(bitmap.Width * 3)];
                for (int y = 0; y < bitmap.Height; y++)
                {
                    Marshal.Copy(IntPtr.Add(data.Scan0, checked(y * data.Stride)), row, 0, row.Length);
                    for (int x = 0; x < row.Length; x++) if (row[x] > 3) return true;
                }
                return false;
            }
            finally { bitmap.UnlockBits(data); }
        }

        public static string Capture(string id, string name, int pid, string started)
        {
            IntPtr hwnd = ParseWindow(id);
            WindowRect before;
            SourceIdentity current = Check(hwnd, id, name, out before);
            RequireIdentity(current, pid, started);
            int width = checked(before.Right - before.Left), height = checked(before.Bottom - before.Top);
            using (Bitmap captured = new Bitmap(width, height, PixelFormat.Format24bppRgb))
            {
                using (Graphics graphics = Graphics.FromImage(captured))
                {
                    graphics.Clear(Color.Black);
                    IntPtr destination = IntPtr.Zero;
                    try
                    {
                        destination = graphics.GetHdc();
                        WindowRect dispatchBounds;
                        RequireIdentity(Check(hwnd, id, name, out dispatchBounds), pid, started);
                        if ((long)dispatchBounds.Right - dispatchBounds.Left != width ||
                            (long)dispatchBounds.Bottom - dispatchBounds.Top != height)
                            throw new InvalidOperationException("capture_failed");
                        if (!PrintWindow(hwnd, destination, 0)) throw new InvalidOperationException("capture_failed");
                    }
                    finally { if (destination != IntPtr.Zero) graphics.ReleaseHdc(destination); }
                }
                WindowRect after;
                RequireIdentity(Check(hwnd, id, name, out after), pid, started);
                if ((long)after.Right - after.Left != width || (long)after.Bottom - after.Top != height)
                    throw new InvalidOperationException("capture_failed");
                double scale = Math.Min(1.0, Math.Min(1366.0 / width, 768.0 / height));
                int outputWidth = Math.Max(1, (int)Math.Floor(width * scale));
                int outputHeight = Math.Max(1, (int)Math.Floor(height * scale));
                using (Bitmap image = new Bitmap(outputWidth, outputHeight, PixelFormat.Format24bppRgb))
                {
                    using (Graphics graphics = Graphics.FromImage(image))
                    {
                        graphics.CompositingMode = CompositingMode.SourceCopy;
                        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                        graphics.DrawImage(captured, new Rectangle(0, 0, outputWidth, outputHeight));
                    }
                    if (!HasVisiblePixel(image)) throw new InvalidOperationException("capture_failed");
                    ImageCodecInfo jpeg = null;
                    foreach (ImageCodecInfo codec in ImageCodecInfo.GetImageEncoders())
                        if (codec.FormatID == ImageFormat.Jpeg.Guid) { jpeg = codec; break; }
                    if (jpeg == null) throw new InvalidOperationException("capture_failed");
                    using (MemoryStream output = new MemoryStream())
                    using (EncoderParameters parameters = new EncoderParameters(1))
                    {
                        parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, 82L);
                        image.Save(output, jpeg, parameters);
                        if (output.Length == 0 || output.Length > MaxJpegBytes)
                            throw new InvalidOperationException("capture_failed");
                        WindowRect finalBounds;
                        RequireIdentity(Check(hwnd, id, name, out finalBounds), pid, started);
                        return Convert.ToBase64String(output.ToArray());
                    }
                }
            }
        }
    }
}
