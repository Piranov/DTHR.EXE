# DTHR.EXE

DTHR.EXE turns images into two-color prints on Windows. Import a PNG, JPEG or WebP, or generate an original locally. Adjust the crop, palette, pixel size and dithering in a live preview. Every edit starts from the saved original.

**[Download the Windows x64 installer](https://github.com/Piranov/DTHR.EXE/releases/latest/download/DTHR-Windows-x64-Setup.exe)**

Run Setup, open DTHR.EXE and choose **File → Import image**. The standalone editor download is about 75 MB. Import, preview and PNG export work offline on the CPU. The first time you choose **Generate**, the app offers to download generation support and asks separately about the selected model.

| Generated original | Two-color print |
| :---: | :---: |
| <img src="docs/botanical-original.png" height="320" alt="Botanical engraving with a flowering stem and exposed roots"> | <img src="docs/botanical-bayer.png" height="320" alt="The same engraving cropped to a narrow panel and rendered with Bayer dithering"> |

The print uses a 4 × 4 Bayer matrix and two colors, `#d8d0c0` and `#111111`, at 220 × 900 pixels.

The desktop app, CLI and local HTTP API share the same processing engine. The [user guide](docs/guide.md) covers editing, automation and running from source.
