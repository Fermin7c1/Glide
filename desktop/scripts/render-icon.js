// Renders the app icon to assets/icon.png (512 x 512) with Electron's own Chromium, so it needs no image tools:
//   npx electron scripts/render-icon.js
// The source is the official artwork, assets/icon-source.png (or .jpg), square, scaled smoothly. The addon's copy,
// addon/Glide/Media/icon.tga, is the same artwork at 64 x 64: make it again when the artwork changes.
const { app, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

const SIZE = 512;
const assets = path.join(__dirname, "..", "assets");
const raster = ["icon-source.png", "icon-source.jpg"].map((f) => path.join(assets, f)).find((p) => fs.existsSync(p));
if (!raster) throw new Error("no assets/icon-source.png or .jpg to render");

function page() {
  const mime = raster.endsWith(".png") ? "image/png" : "image/jpeg";
  const data = fs.readFileSync(raster).toString("base64");
  return `<img src="data:${mime};base64,${data}" style="display:block;width:${SIZE}px;height:${SIZE}px;image-rendering:auto">`;
}

app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: SIZE, height: SIZE, show: false, frame: false, transparent: true,
                                  useContentSize: true, webPreferences: { offscreen: true } });
  const html = `<html><body style="margin:0;background:transparent;overflow:hidden">${page()}</body></html>`;
  await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(html));
  await new Promise((r) => setTimeout(r, 400));
  const img = await win.webContents.capturePage({ x: 0, y: 0, width: SIZE, height: SIZE });
  fs.writeFileSync(path.join(assets, "icon.png"), img.resize({ width: SIZE, height: SIZE, quality: "best" }).toPNG());
  console.log("wrote assets/icon.png from", path.basename(raster), img.getSize());
  app.quit();
});
