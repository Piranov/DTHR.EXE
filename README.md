# DTHR.EXE

DTHR.EXE turns images into two-color prints on Windows. Import a PNG, JPEG or WebP, or generate an original locally. Adjust the crop, palette, pixel size and dithering in a live preview. Every edit starts from the saved original.

**[Download the Windows x64 installer (75 MB)](https://github.com/Piranov/DTHR.EXE/releases/latest/download/DTHR-Windows-x64-Setup.exe)**

**Dithering your own images?** The installer is the only file you need. Run it, open **File → Import image**, and dither and export PNGs offline using your CPU.

**Want to generate an image?** Choose **Generate**. On first use, DTHR.EXE asks for confirmation and downloads the optional GPU component (2.98 GB) for you. It asks separately before downloading any missing model weights. The generation ZIPs on the release page are for manual offline setup.

| Generated original | Two-color print |
| :---: | :---: |
| <img src="docs/botanical-original.png" height="320" alt="Botanical engraving with a flowering stem and exposed roots"> | <img src="docs/botanical-bayer.png" height="320" alt="The same engraving cropped to a narrow panel and rendered with Bayer dithering"> |

The print uses a 4 × 4 Bayer matrix and two colors, `#d8d0c0` and `#111111`, at 220 × 900 pixels.

The desktop app, CLI and local HTTP API share the same processing engine. The [user guide](docs/guide.md) covers editing, automation and running from source.
