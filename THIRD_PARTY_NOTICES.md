# Third-party notices

The MIT License in this repository covers only the original source code of
AI Media Synthetic Marker. Portable release archives also contain third-party
software under its own license terms.

## ExifTool 13.59

- Author: Phil Harvey
- Website: https://exiftool.org/
- Source: https://github.com/exiftool/exiftool
- License: the same terms as Perl itself (the Artistic License or the GNU GPL)

The Windows package must keep `exiftool.exe` and `exiftool_files` together.
The release archive preserves the license and notice files supplied with the
official Windows package.

## ExifTool Windows package

- Package maintainer: Oliver Betz
- Information: https://oliverbetz.de/pages/Artikel/ExifTool-for-Windows
- Launcher: CC0, as stated in the bundled `readme_windows.txt`
- Strawberry Perl and bundled modules: see
  `exiftool/exiftool_files/Licenses_Strawberry_Perl.zip`

## CPython 3.14.6

- Copyright: Python Software Foundation and contributors
- Website: https://www.python.org/
- License: PSF License Agreement and the additional notices in the Python
  distribution

The portable release includes a copy of the Python license under `licenses/`.

## Tcl/Tk 8.6

- Website: https://www.tcl.tk/
- License: Tcl/Tk license terms

Tkinter and the required Tcl/Tk runtime are bundled by PyInstaller. The
portable release includes separate Tcl 8.6 and Tk 8.6 license terms under
`licenses/`. The source copies used by the build are kept in
`packaging/licenses/`.

## PyInstaller 6.21.0

- Website: https://pyinstaller.org/
- Source: https://github.com/pyinstaller/pyinstaller
- License: GPL with the special exception applying to bundled applications

The portable release includes PyInstaller's `COPYING.txt` under `licenses/`.

This notice is informational and does not replace the complete license files
distributed with each component.
