using System;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace ScannerDetector
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new DetectorForm());
        }
    }

    internal sealed class DetectorForm : Form
    {
        private readonly TextBox output;
        private readonly Button scan;
        private readonly Button save;

        public DetectorForm()
        {
            Text = "Scanner Detector (32-bit)";
            Width = 760;
            Height = 600;
            StartPosition = FormStartPosition.CenterScreen;

            var toolbar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 44, Padding = new Padding(8) };
            scan = new Button { Text = "다시 검색", AutoSize = true };
            save = new Button { Text = "결과 저장...", AutoSize = true };
            scan.Click += (s, e) => ScanDevices();
            save.Click += (s, e) => SaveReport();
            toolbar.Controls.Add(scan);
            toolbar.Controls.Add(save);

            output = new TextBox
            {
                Dock = DockStyle.Fill,
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Both,
                WordWrap = false,
                Font = new System.Drawing.Font("Consolas", 10),
                BackColor = System.Drawing.SystemColors.Window
            };
            Controls.Add(output);
            Controls.Add(toolbar);
            Shown += (s, e) => ScanDevices();
        }

        private void ScanDevices()
        {
            scan.Enabled = false;
            var report = new StringBuilder();
            report.AppendLine("Scanner Detector");
            report.AppendLine("Process: " + (Environment.Is64BitProcess ? "64-bit" : "32-bit"));
            report.AppendLine("OS: " + (Environment.Is64BitOperatingSystem ? "64-bit" : "32-bit"));
            report.AppendLine();
            report.AppendLine("=== TWAIN 32-bit Sources ===");
            try
            {
                var sources = TwainEnumerator.Enumerate();
                if (sources.Count == 0) report.AppendLine("(등록된 Source 없음)");
                foreach (var source in sources) report.AppendLine(source);
            }
            catch (Exception ex)
            {
                report.AppendLine("열거 실패: " + ex.Message);
                report.AppendLine("DSM이 설치되지 않았거나 초기화할 수 없습니다.");
            }

            report.AppendLine();
            report.AppendLine("=== WIA Scanner Devices ===");
            try
            {
                var devices = WiaEnumerator.Enumerate();
                if (devices.Count == 0) report.AppendLine("(등록된 WIA 스캐너 없음)");
                foreach (var device in devices) report.AppendLine(device);
            }
            catch (Exception ex)
            {
                report.AppendLine("열거 실패: " + ex.Message);
                report.AppendLine("WIA 서비스/COM 구성요소 접근을 확인하세요.");
            }
            report.AppendLine();
            report.AppendLine("판매점 프로그램의 장치 목록과 위 두 목록을 비교하세요.");
            output.Text = report.ToString();
            scan.Enabled = true;
        }

        private void SaveReport()
        {
            using (var dialog = new SaveFileDialog { Filter = "Text file (*.txt)|*.txt", FileName = "scanner-detector.txt" })
                if (dialog.ShowDialog(this) == DialogResult.OK) File.WriteAllText(dialog.FileName, output.Text, new UTF8Encoding(true));
        }
    }
}
