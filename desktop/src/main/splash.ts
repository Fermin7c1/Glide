// The "Updating Glide" window shown while an update installs. The installer runs silently (no setup wizard), and it
// replaces Glide's own files, so the window can't belong to Glide: it's a small Windows Forms window in a detached
// PowerShell process that outlives the app. It opens over the middle of Glide's own window (whichever monitor that
// is on), says when it's up (the ready file, so Glide closes only once it shows), and its bar fills as an install
// usually goes (about 20 s), easing toward the end until the new Glide is up (a "Glide to 60" process that started
// after it): then it fills and closes. After three minutes it closes whatever happens.
import { spawn } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";

const SCRIPT = String.raw`
param([string]$Version, [string]$Icon, [string]$Preview, [string]$CX, [string]$CY, [string]$Ready)
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Namespace Glide -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[Glide.Dpi]::SetProcessDPIAware() | Out-Null
$since = Get-Date
$g = [Drawing.Graphics]::FromHwnd([IntPtr]::Zero); $k = $g.DpiX / 96; $g.Dispose()
function px([double]$v) { [int][math]::Round($v * $k) }
$navy = [Drawing.Color]::FromArgb(12, 13, 18); $track = [Drawing.Color]::FromArgb(34, 37, 46)
$gold = [Drawing.Color]::FromArgb(242, 194, 79); $ink2 = [Drawing.Color]::FromArgb(186, 181, 168)

$f = New-Object Windows.Forms.Form
$f.Text = 'Updating Glide'; $f.FormBorderStyle = 'None'; $f.StartPosition = 'CenterScreen'; $f.TopMost = $true
$f.BackColor = $navy; $f.ClientSize = New-Object Drawing.Size((px 450), (px 128)); $f.ShowInTaskbar = $true
if ($CX -and $CY) {                                              # over the middle of Glide's own window
  $f.StartPosition = 'Manual'
  $f.Location = New-Object Drawing.Point(([int]$CX - [int]($f.Width / 2)), ([int]$CY - [int]($f.Height / 2)))
}
if ($Icon -and (Test-Path $Icon)) {
  $bytes = [IO.File]::ReadAllBytes($Icon)                       # read once: the file isn't kept open
  $img = [Drawing.Image]::FromStream((New-Object IO.MemoryStream(, $bytes)))
  $pic = New-Object Windows.Forms.PictureBox
  # the icon, as tall as the title, the line and the bar beside it
  $pic.Image = $img; $pic.SizeMode = 'Zoom'; $pic.Location = New-Object Drawing.Point((px 24), (px 27)); $pic.Size = New-Object Drawing.Size((px 74), (px 74))
  $f.Controls.Add($pic)
  try { $f.Icon = [Drawing.Icon]::FromHandle(([Drawing.Bitmap]$img).GetHicon()) } catch { }
}
$title = New-Object Windows.Forms.Label
$title.Text = 'Updating Glide'; $title.ForeColor = $gold; $title.AutoSize = $true
$title.Font = New-Object Drawing.Font('Segoe UI Semibold', 14); $title.Location = New-Object Drawing.Point((px 116), (px 24))
$sub = New-Object Windows.Forms.Label
$sub.Text = "Installing version $Version. Glide opens again in a moment."; $sub.ForeColor = $ink2; $sub.AutoSize = $true
$sub.Font = New-Object Drawing.Font('Segoe UI', 9.5); $sub.Location = New-Object Drawing.Point((px 117), (px 58))
$rail = New-Object Windows.Forms.Panel
$rail.BackColor = $track; $rail.Location = New-Object Drawing.Point((px 118), (px 95)); $rail.Size = New-Object Drawing.Size((px 298), (px 6))
$bar = New-Object Windows.Forms.Panel
$bar.BackColor = $gold; $bar.Size = New-Object Drawing.Size(0, (px 6)); $bar.Location = New-Object Drawing.Point(0, 0)
$rail.Controls.Add($bar)
$f.Controls.AddRange(@($title, $sub, $rail))

if ($Preview) {                                                  # tests: draw it to a PNG, never on screen
  $bmp = New-Object Drawing.Bitmap($f.ClientSize.Width, $f.ClientSize.Height)
  $gr = [Drawing.Graphics]::FromImage($bmp); $gr.Clear($navy); $gr.Dispose()
  $bar.Width = [int]($rail.Width * 0.55)                         # mid-install
  foreach ($c in $f.Controls) {                                  # a never-shown form doesn't paint its children itself
    $null = $c.Handle
    $c.DrawToBitmap($bmp, (New-Object Drawing.Rectangle($c.Left, $c.Top, $c.Width, $c.Height)))
  }
  $bmp.Save($Preview, [Drawing.Imaging.ImageFormat]::Png)
  exit 0
}

# the bar: an install takes about 20 s; it fills toward 92% on that pace (slowing as it nears), the rest when the new
# Glide is up
$done = $false
$anim = New-Object Windows.Forms.Timer; $anim.Interval = 30
$anim.Add_Tick({
  $t = ((Get-Date) - $since).TotalSeconds
  $p = if ($script:done) { 1 } else { 0.92 * (1 - [math]::Exp(-$t / 9)) }
  $bar.Width = [int]($rail.Width * $p)
})
$seen = 0
$watch = New-Object Windows.Forms.Timer; $watch.Interval = 700
$watch.Add_Tick({
  $new = Get-Process -Name 'Glide to 60' | Where-Object { $_.StartTime -gt $since -and $_.MainWindowHandle -ne 0 }
  if (-not $new) { $new = Get-Process -Name 'Glide to 60' | Where-Object { $_.StartTime -gt $since.AddSeconds(2) } }
  if ($new) { $script:seen++ } else { $script:seen = 0 }
  if ($script:seen -ge 1 -and -not $script:done) { $script:done = $true; $sub.Text = 'Opening Glide...' }
  if ($script:seen -ge 2 -or ((Get-Date) - $since).TotalSeconds -gt 180) { $f.Close() }
})
$f.Add_Shown({
  $anim.Start(); $watch.Start()
  if ($Ready) { Set-Content -Path $Ready -Value 'shown' }          # Glide closes once this window is up
})
[Windows.Forms.Application]::Run($f)
`;

/** Write the script and the icon next to each other in the temp folder (the install folder is about to be replaced). */
function prepare(iconPath: string): { script: string; icon: string } {
  const dir = path.join(os.tmpdir(), "glide-update");
  fs.mkdirSync(dir, { recursive: true });
  const script = path.join(dir, "updating.ps1");
  fs.writeFileSync(script, "﻿" + SCRIPT, "utf8");      // a real BOM: Windows PowerShell reads the file as UTF-8
  const icon = path.join(dir, "icon.png");
  try {
    fs.writeFileSync(icon, fs.readFileSync(iconPath));        // read through Electron's fs: works inside app.asar
  } catch {
    /* no icon: the window still shows */
  }
  return { script, icon };
}

/** Show the "Updating Glide" window in its own process (it outlives the app), centred on `centre` (screen pixels:
 * the middle of Glide's window) when given. Resolves once the window is on screen, or after `waitMs` whatever
 * happens (a slow or failed start must never hold the update back). */
export function showUpdateSplash(version: string, iconPath: string, centre?: { x: number; y: number },
                                 waitMs = 5000): Promise<boolean> {
  if (process.platform !== "win32") return Promise.resolve(false);
  // it goes through cmd (below), which re-reads its arguments: a version is only ever 1.2.3-style text
  if (!/^[0-9A-Za-z.+-]{1,40}$/.test(version)) version = "";
  const { script, icon } = prepare(iconPath);
  const ready = path.join(path.dirname(script), "shown.txt");
  fs.rmSync(ready, { force: true });
  const args = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script,
    "-Version", version, "-Icon", icon, "-Ready", ready];
  if (centre) args.push("-CX", String(Math.round(centre.x)), "-CY", String(Math.round(centre.y)));
  // through cmd's start: a detached PowerShell has no console and quits before it draws anything (the window never
  // showed); start gives it one of its own (minimised, then hidden by -WindowStyle), and detached lets it outlive Glide
  const p = spawn("cmd", ["/c", "start", '""', "/min", "powershell", ...args], { detached: true, stdio: "ignore", windowsHide: true });
  p.unref();
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = setInterval(() => {
      const shown = fs.existsSync(ready);
      if (shown || Date.now() - t0 > waitMs) {
        clearInterval(tick);
        resolve(shown);
      }
    }, 100);
  });
}

/** Render the window to a PNG without showing it (tests and screenshots). */
export function previewUpdateSplash(version: string, iconPath: string, out: string): Promise<number> {
  const { script, icon } = prepare(iconPath);
  return new Promise((resolve) => {
    const p = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Version", version,
      "-Icon", icon, "-Preview", out], { stdio: "ignore", windowsHide: true });
    p.on("exit", (code) => resolve(code ?? 1));
  });
}
