# CDF to PDF Converter

## Branded iQ+ batch printer

The current desktop application is `iqplus_batch_print.py`. It uses the installed
iQ+ Waveform Viewer and Microsoft Print to PDF. PDFs default to the source CDF's
folder and filename. The executable includes the logo and Q icon.

```powershell
pip install -r requirements.txt
python iqplus_batch_print.py
pyinstaller --clean Qualitrol-Batch-PDF.spec
```

Copy `dist/Qualitrol-Batch-PDF.exe` to a compatible Windows laptop with iQ+ and
Microsoft Print to PDF installed. Python is not needed to run the executable.
The interactive desktop must remain available during conversion.

The standalone renderer below is an earlier implementation and does not guarantee
the same values or layout as iQ+.

A small Windows-friendly Python app for converting `.cdf` files to `.pdf`.

CDF can refer to different formats. This app supports these practical paths:

- IQ+/Carrick/Informa DFR waveform files by plotting the actual `.dat` waveform samples in an IQ-style PDF.
- IQ+/Carrick/Informa trend-style files by creating a readable waveform/report PDF from the XML metadata.
- Wolfram/Mathematica CDF files when `wolframscript` is installed.
- NASA Common Data Format files by creating a readable PDF summary of the file metadata and variables.

If the CDF file is a proprietary visual document and no exporter is installed, the app will show a clear error explaining what is missing.

## Run on this laptop

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

In the desktop app, use **Browse** to choose one or more `.cdf` files, choose an output folder, then press **Convert to PDF**. The app creates one `.pdf` for each selected `.cdf`.

## Command-line use

```powershell
python app.py "C:\path\input.cdf" "C:\path\output.pdf"
```

## Make it usable on another Windows laptop

Build a portable executable:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pyinstaller --onefile --windowed --name CDF-to-PDF app.py
```

Then copy this file to the other laptop:

```text
dist\CDF-to-PDF.exe
```

If you rebuild with the included spec file, use:

```powershell
pyinstaller CDF-to-PDF.spec
```

Notes:

- NASA CDF summary conversion is bundled into the executable.
- IQ+/Carrick/Informa waveform conversion is bundled into the executable.
- Wolfram CDF export still requires Wolfram Engine, Mathematica, or another installation that provides `wolframscript` on that laptop.

## iQ+ exact-output batch printing

For Qualitrol iQ+ records where the PDF must match iQ+ exactly, use the iQ+ batch printer instead of the standalone renderer. This tool opens each `.cdf` in iQ+, sends **Print Full Record Length**, chooses **Microsoft Print to PDF**, saves the PDF, then moves to the next file.

Built executables:

```text
dist\IQPlus-Batch-PDF-Printer.exe
dist\IQPlus-Batch-PDF-Printer-Console.exe
```

Normal use:

```powershell
.\dist\IQPlus-Batch-PDF-Printer.exe
```

Command prompt use:

```powershell
.\dist\IQPlus-Batch-PDF-Printer-Console.exe "C:\path\one.cdf" "C:\path\two.cdf" -o "C:\path\pdf output"
```

Drag-and-drop use:

- Drag one or more `.cdf` files onto `IQPlus-Batch-PDF-Printer.exe`.
- If no output folder is chosen, each PDF is saved beside its matching CDF.

Important:

- iQ+ must be installed on the laptop.
- `.cdf` files must open in iQ+ when double-clicked.
- Leave the computer alone while the batch is running because the tool controls iQ+ and the Windows print dialogs.
