from __future__ import annotations

import argparse
import cmath
import html
import math
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from math import pi, sin
from pathlib import Path
from tkinter import END, MULTIPLE, Tk, filedialog, messagebox, ttk, StringVar, Listbox

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors


class ConversionError(Exception):
    pass


def convert_cdf_to_pdf(input_path: Path, output_path: Path) -> str:
    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()

    if not input_path.exists():
        raise ConversionError(f"Input file does not exist: {input_path}")
    if input_path.suffix.lower() != ".cdf":
        raise ConversionError("Please choose a file with the .cdf extension.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    wolfram_error = None
    if shutil.which("wolframscript"):
        try:
            _convert_with_wolfram(input_path, output_path)
            return "Converted with WolframScript."
        except ConversionError as exc:
            wolfram_error = str(exc)

    try:
        _convert_zipped_xml_cdf(input_path, output_path)
        return "Created a PDF report from the XML data inside this CDF file."
    except ConversionError:
        pass

    try:
        _convert_nasa_cdf_summary(input_path, output_path)
        return "Created a PDF summary from a NASA Common Data Format file."
    except ConversionError as nasa_error:
        extra = f"\n\nWolframScript error: {wolfram_error}" if wolfram_error else ""
        raise ConversionError(
            "Could not convert this CDF file automatically.\n\n"
            "If this is a Wolfram CDF document, install Wolfram Engine or Mathematica "
            "on this laptop and make sure the 'wolframscript' command is available."
            f"\n\nNASA CDF summary error: {nasa_error}{extra}"
        ) from nasa_error


def _convert_with_wolfram(input_path: Path, output_path: Path) -> None:
    script = (
        f'Export["{_wolfram_path(output_path)}", '
        f'Import["{_wolfram_path(input_path)}", "CDF"]]'
    )
    result = subprocess.run(
        ["wolframscript", "-code", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or "Unknown Wolfram export error").strip()
        raise ConversionError(detail)


def _wolfram_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace('"', '\\"')


def _convert_nasa_cdf_summary(input_path: Path, output_path: Path) -> None:
    try:
        import cdflib
    except ImportError as exc:
        raise ConversionError("The cdflib package is not installed.") from exc

    try:
        cdf = cdflib.CDF(str(input_path))
        info = cdf.cdf_info()
    except Exception as exc:
        raise ConversionError(str(exc)) from exc

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(output_path), pagesize=A4, title=input_path.name)
    story = [
        Paragraph(f"CDF Summary: {input_path.name}", styles["Title"]),
        Spacer(1, 12),
        Paragraph(str(input_path), styles["BodyText"]),
        Spacer(1, 18),
    ]

    metadata_rows = [["Field", "Value"]]
    for key in ("CDF", "Version", "Encoding", "Majority", "rVariables", "zVariables", "Attributes"):
        value = getattr(info, key, None)
        if value is None and isinstance(info, dict):
            value = info.get(key)
        metadata_rows.append([key, _short(value)])
    story.append(_table(metadata_rows))
    story.append(Spacer(1, 18))

    variables = list(getattr(info, "rVariables", []) or []) + list(getattr(info, "zVariables", []) or [])
    if variables:
        story.append(Paragraph("Variables", styles["Heading2"]))
        rows = [["Name", "Type", "Shape", "Records"]]
        for name in variables:
            try:
                spec = cdf.varinq(name)
                rows.append([
                    str(name),
                    _short(getattr(spec, "Data_Type_Description", getattr(spec, "Data_Type", ""))),
                    _short(getattr(spec, "Dim_Sizes", "")),
                    _short(getattr(spec, "Last_Rec", "")),
                ])
            except Exception:
                rows.append([str(name), "", "", ""])
        story.append(_table(rows))
    else:
        story.append(Paragraph("No variables were found in this CDF file.", styles["BodyText"]))

    try:
        doc.build(story)
    except Exception as exc:
        raise ConversionError(f"Could not write PDF: {exc}") from exc


def _convert_zipped_xml_cdf(input_path: Path, output_path: Path) -> None:
    try:
        with zipfile.ZipFile(input_path) as archive:
            xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if not xml_names:
                raise ConversionError("No XML file found inside the CDF container.")
            xml_data = archive.read(xml_names[0])
            archive_entries = [(item.filename, item.file_size) for item in archive.infolist()]
            dat_payloads = {
                name: archive.read(name)
                for name in archive.namelist()
                if name.lower().endswith(".dat")
            }
    except zipfile.BadZipFile as exc:
        raise ConversionError("This is not a ZIP-style CDF container.") from exc
    except Exception as exc:
        raise ConversionError(str(exc)) from exc

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise ConversionError(f"Could not read XML inside CDF: {exc}") from exc

    summary_fields = [
        ("Created on", _find_text(root, "CreatedOn")),
        ("Version", _find_text(root, "Version")),
        ("Record type", _find_text(root, "RecordType")),
        ("Station", _find_text(root, "StationName")),
        ("Device", _find_text(root, "DeviceName")),
        ("Device type", _find_text(root, "DeviceType")),
        ("Line frequency", _find_text(root, "LineFrequency")),
        ("Time locked", _find_text(root, "TimeLocked")),
        ("Sample rate", _find_text(root, "SampleRateInHz")),
        ("Total samples", _find_text(root, "TotalNumberOfSamples")),
        ("Start time seconds", _find_text(root, "StartTimeSeconds")),
        ("End time seconds", _find_text(root, "EndTimeinSeconds")),
    ]

    analog_channels = _find_all(root, "AnalogChannel")
    bands = _find_all(root, "Band")

    try:
        samples = _decode_float_samples(root, analog_channels, dat_payloads)
        _write_waveform_pdf(
            output_path,
            input_path.name,
            root,
            summary_fields,
            analog_channels,
            bands,
            archive_entries,
            samples,
        )
    except Exception as exc:
        raise ConversionError(f"Could not write waveform PDF: {exc}") from exc


def _write_waveform_pdf(
    output_path: Path,
    title: str,
    root: ET.Element,
    summary_fields: list[tuple[str, str]],
    analog_channels: list[ET.Element],
    bands: list[ET.Element],
    archive_entries: list[tuple[str, int]],
    samples: list[list[float]] | None,
) -> None:
    record_type = _find_text(root, "RecordType").upper()
    channel_rows = _channel_rows(root, analog_channels, bands)
    samples = _append_calculated_samples(root, channel_rows, samples)
    if _is_merged_cross_trigger_root(root):
        visible_rows = [row for row in channel_rows if not row.get("calculated") and not row.get("digital")]
        visible_rows.extend(row for row in channel_rows if row.get("digital") and row.get("visible"))
    else:
        visible_rows = [row for row in channel_rows if row["visible"]][:] if channel_rows else []

    page_size = landscape(letter)
    page_width, page_height = page_size
    pdf = canvas.Canvas(str(output_path), pagesize=page_size)
    pdf.setTitle(title)

    margin = 28
    label_width = 245 if record_type.startswith("DDR") else 180
    top = page_height - 44
    bottom = 36
    graph_left = margin + label_width
    graph_right = page_width - margin
    graph_width = graph_right - graph_left
    compact_page = False

    channels_per_page = 7
    page_specs = _waveform_page_specs(root, visible_rows, samples, channels_per_page)
    waveform_pages = len(page_specs)

    for page_index, page_spec in enumerate(page_specs):
        page_rows, window_start, window_end = page_spec[:3]
        page_label = (page_spec[3], page_spec[4]) if len(page_spec) > 4 else None
        row_weights = [_row_weight(row) for row in page_rows]
        row_unit = (top - bottom - 12) / max(sum(row_weights), 1)
        _draw_iq_header(pdf, root, page_width, page_height, margin)
        _draw_time_axis(pdf, root, graph_left, graph_right, top, bottom, window_start, window_end)
        _draw_cursors(pdf, root, graph_left, graph_width, top, bottom, window_start, window_end)

        current_top = top
        for local_index, row in enumerate(page_rows):
            channel_index = int(row.get("sample_index", page_index * channels_per_page + local_index))
            row_height = row_unit * _row_weight(row)
            y_top = current_top
            y_mid = y_top - row_height / 2
            y_bottom = y_top - row_height
            current_top = y_bottom
            colour = row["colour"]

            pdf.setStrokeColor(colors.HexColor("#d0d0d0"))
            pdf.setLineWidth(0.3)
            pdf.line(graph_left, y_mid, graph_right, y_mid)
            pdf.line(graph_left, y_bottom, graph_left + 4, y_bottom)

            pdf.setFillColor(colour)
            pdf.setFont("Helvetica", 6)
            first_value_x = margin + 22
            second_value_x = margin + 62
            unit_x = margin + 82
            name_x = margin + 102
            pdf.drawRightString(first_value_x, y_mid + 2, _cursor_value(samples, channel_index, root, "RedCursorSampleIndex", row))
            pdf.drawRightString(second_value_x, y_mid + 2, _cursor_value(samples, channel_index, root, "BlueCursorSampleIndex", row))
            pdf.drawCentredString(unit_x, y_mid + 2, _display_unit(row))
            pdf.drawString(name_x, y_mid + 2, _display_channel_name(row))
            if not row.get("digital"):
                pdf.drawRightString(graph_left - 5, y_mid + row_height * 0.34, _display_value(row, row["max"]))
                pdf.drawRightString(graph_left - 5, y_mid - row_height * 0.34, _display_value(row, row["min"]))

            points = _sample_points(samples, channel_index, row, graph_left, graph_width, y_mid, row_height, window_start, window_end)
            if not points:
                points = _synthetic_points(local_index, row, graph_left, graph_width, y_mid, row_height)

            pdf.setStrokeColor(colour)
            pdf.setLineWidth(0.45)
            path = pdf.beginPath()
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            pdf.drawPath(path)

        _draw_iq_footer(pdf, root, page_width, margin, page_index + 1, waveform_pages, page_label)
        pdf.showPage()

    if samples:
        pdf.save()
        return

    pdf.setPageSize(A4)
    page_width, page_height = A4
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, page_height - 36, "CDF Details")
    pdf.setFont("Helvetica", 8)
    y = page_height - 60
    for name, value in summary_fields:
        pdf.drawString(margin, y, f"{name}:")
        pdf.drawString(margin + 110, y, _short(value or "", 72))
        y -= 13
    y -= 8
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(margin, y, "Container Files")
    y -= 15
    pdf.setFont("Helvetica", 8)
    for name, size in archive_entries:
        pdf.drawString(margin, y, _short(name, 72))
        pdf.drawRightString(page_width - margin, y, f"{size:,} bytes")
        y -= 13
    pdf.save()


def _decode_float_samples(
    root: ET.Element,
    analog_channels: list[ET.Element],
    dat_payloads: dict[str, bytes],
) -> list[list[float]] | None:
    import struct

    if not dat_payloads:
        return None

    try:
        sample_count = int(_find_text(root, "TotalNumberOfSamples"))
    except ValueError:
        return None

    analog_count = len(analog_channels)
    if sample_count <= 0 or analog_count <= 0:
        return None

    record_type = _find_text(root, "RecordType").upper()
    if record_type.startswith("DDR") or record_type.startswith("TSS"):
        segmented = _decode_segmented_ddr_samples(dat_payloads, analog_count)
        if segmented:
            return segmented

    payload = b"".join(dat_payloads[name] for name in sorted(dat_payloads))
    float_count = len(payload) // 4
    values = struct.unpack("<" + "f" * float_count, payload[: float_count * 4])
    full_blocks = float_count // sample_count

    if record_type.startswith("DDR") or record_type.startswith("TSS"):
        if full_blocks < analog_count + 2:
            return _decode_compact_ddr_samples(values, sample_count, analog_channels)
        # DDR/TSS trend files store two channel-sized timestamp arrays first:
        # seconds and microseconds. The analogue trend arrays follow them.
        offset = sample_count * 2
    else:
        offset = 0

    required = offset + sample_count * analog_count
    if len(values) < required:
        return None

    channels: list[list[float]] = []
    for channel_index in range(analog_count):
        start = offset + channel_index * sample_count
        end = start + sample_count
        channel = [value for value in values[start:end] if abs(value) < 1e20]
        channels.append(channel)
    return channels


def _decode_segmented_ddr_samples(
    dat_payloads: dict[str, bytes],
    analog_count: int,
) -> list[list[float]] | None:
    import struct

    channels = [[] for _ in range(analog_count)]
    decoded_any = False
    for name in sorted(dat_payloads):
        payload = dat_payloads[name]
        float_count = len(payload) // 4
        block_count = analog_count + 2
        if float_count % block_count != 0:
            return None
        segment_samples = float_count // block_count
        if segment_samples <= 0:
            return None
        values = struct.unpack("<" + "f" * float_count, payload[: float_count * 4])
        offset = segment_samples * 2
        for channel_index in range(analog_count):
            start = offset + channel_index * segment_samples
            end = start + segment_samples
            channels[channel_index].extend(values[start:end])
        decoded_any = True
    return channels if decoded_any else None


def _decode_compact_ddr_samples(
    values: tuple[float, ...],
    sample_count: int,
    analog_channels: list[ET.Element],
) -> list[list[float]] | None:
    full_blocks = len(values) // sample_count
    if full_blocks < 2:
        return None

    stored = []
    for block_index in range(1, full_blocks):
        start = block_index * sample_count
        end = start + sample_count
        block = list(values[start:end])
        stored.append(block)
    if not stored:
        return None

    def pick(channel: ET.Element) -> list[float]:
        name = _child_text(channel, "ChannelName").upper()
        unit = _child_text(channel, "ChannelUnit").upper()
        if "FREQUENCY" in name:
            return stored[0]
        if unit == "V":
            return stored[min(1, len(stored) - 1)]
        if "IY" in name and len(stored) > 3:
            return stored[3]
        if "IB" in name and len(stored) > 4:
            return stored[4]
        if "CURRENT-SPARE" in name and len(stored) > 4:
            return stored[4]
        if unit == "A" and len(stored) > 2:
            return stored[2]
        return stored[-1]

    return [pick(channel) for channel in analog_channels]


def _waveform_page_specs(
    root: ET.Element,
    rows: list[dict[str, object]],
    samples: list[list[float]] | None,
    channels_per_page: int,
) -> list[tuple[list[dict[str, object]], int | None, int | None]]:
    record_type = _find_text(root, "RecordType").upper()
    analog_rows = [row for row in rows if not row.get("calculated")]
    digital_rows = [row for row in analog_rows if row.get("digital")]
    analog_rows = [row for row in analog_rows if not row.get("digital")]
    calculation_rows = [row for row in rows if row.get("calculated")]

    sample_count = len(samples[0]) if samples and samples[0] else 0
    if _is_merged_cross_trigger(analog_rows):
        return _merged_cross_trigger_specs(analog_rows, digital_rows)
    if _is_fault_distance_record(root, analog_rows, digital_rows):
        return _fault_distance_specs(analog_rows, digital_rows, sample_count)

    if record_type.startswith("DDRT") and sample_count:
        rate = _sample_rate(root)
        window_size = min(sample_count, int(rate * 200))
        try:
            red_cursor = int(_find_text(root, "RedCursorSampleIndex"))
        except ValueError:
            red_cursor = sample_count // 2
        start = max(0, min(sample_count - window_size, red_cursor - int(rate * 90)))
        end = start + window_size
        specs = []
        for index in range(0, len(analog_rows), channels_per_page):
            specs.append((analog_rows[index : index + channels_per_page], start, end))
        if calculation_rows:
            for index in range(0, len(calculation_rows), channels_per_page):
                specs.append((calculation_rows[index : index + channels_per_page], start, end))
        return specs or [(rows, start, end)]

    if record_type == "DDRC" and sample_count > 60000:
        window_count = 5
        window_size = max(1, int(round(_sample_rate(root) * 400)))
        if sample_count == int(round(_sample_rate(root) * 1800)):
            window_starts = [0, 400, 800, 1100, 1500]
        else:
            window_starts = [window * 400 for window in range(window_count)]
        row_groups = [analog_rows[index : index + channels_per_page] for index in range(0, len(analog_rows), channels_per_page)]
        specs = []
        for group_index, row_group in enumerate(row_groups, start=1):
            for window, start_seconds in enumerate(window_starts):
                start = min(sample_count, int(round(_sample_rate(root) * start_seconds)))
                end = min(sample_count, start + window_size)
                specs.append((row_group, start, end, f"{group_index}.{window + 1}", f"{len(row_groups)} x {window_count}"))
        return specs or [(rows, None, None)]

    specs = []
    if digital_rows and not calculation_rows:
        combined_rows = analog_rows + digital_rows
        page_rows: list[dict[str, object]] = []
        page_weight = 0.0
        max_page_weight = 10.3
        for row in combined_rows:
            weight = _row_weight(row)
            if page_rows and page_weight + weight > max_page_weight:
                specs.append((page_rows, None, None))
                page_rows = []
                page_weight = 0.0
            page_rows.append(row)
            page_weight += weight
        if page_rows:
            specs.append((page_rows, None, None))
        return specs or [(rows, None, None)]

    for index in range(0, len(analog_rows), channels_per_page):
        specs.append((analog_rows[index : index + channels_per_page], None, None))
    if calculation_rows:
        if len(calculation_rows) == 5 and any("Unbalance" in str(row.get("name", "")) for row in calculation_rows):
            if specs and len(specs[-1][0]) < channels_per_page:
                existing_rows, start, end = specs[-1]
                specs[-1] = (existing_rows + digital_rows + calculation_rows[:1], start, end)
            else:
                specs.append((digital_rows + calculation_rows[:1], None, None))
            specs.append((calculation_rows[1:], None, None))
            return specs or [(rows, None, None)]
        if digital_rows:
            specs.append((digital_rows, None, None))
        if specs and len(specs[-1][0]) < channels_per_page:
            existing_rows, start, end = specs[-1]
            spare = channels_per_page - len(existing_rows)
            specs[-1] = (existing_rows + calculation_rows[:spare], start, end)
            calculation_rows = calculation_rows[spare:]
        for index in range(0, len(calculation_rows), channels_per_page):
            specs.append((calculation_rows[index : index + channels_per_page], None, None))
    return specs or [(rows, None, None)]


def _is_merged_cross_trigger(analog_rows: list[dict[str, object]]) -> bool:
    names = [str(row.get("name", "")) for row in analog_rows]
    return any("[m1]" in name for name in names) and any("[m2]" in name for name in names)


def _is_fault_distance_record(
    root: ET.Element,
    analog_rows: list[dict[str, object]],
    digital_rows: list[dict[str, object]],
) -> bool:
    return (
        _find_text(root, "RecordType").upper() == "DFR"
        and _find_text(root, "CauseOfTrigger") == "1"
        and len(analog_rows) == 9
        and len(digital_rows) >= 20
        and not _is_merged_cross_trigger(analog_rows)
    )


def _fault_distance_specs(
    analog_rows: list[dict[str, object]],
    digital_rows: list[dict[str, object]],
    sample_count: int,
) -> list[tuple[list[dict[str, object]], int | None, int | None, str, str]]:
    page_span = max(1, math.ceil(sample_count / 2)) if sample_count else 12800
    groups = [
        analog_rows[0:3],
        analog_rows[3:6],
        analog_rows[6:9],
        digital_rows[:20],
        digital_rows[20:],
    ]
    for row in digital_rows:
        row["compact_digital"] = True
    specs = []
    for group_index, group in enumerate(groups, start=1):
        specs.append((group, 0, page_span, f"{group_index}.1", "5 x 2"))
        specs.append((group, page_span, sample_count or page_span * 2, f"{group_index}.2", "5 x 2"))
    return specs


def _merged_cross_trigger_specs(
    analog_rows: list[dict[str, object]],
    digital_rows: list[dict[str, object]],
) -> list[tuple[list[dict[str, object]], int | None, int | None]]:
    def find(prefix: str, token: str) -> dict[str, object] | None:
        for row in analog_rows:
            name = str(row.get("name", ""))
            upper_name = name.upper()
            upper_token = token.upper()
            if prefix in name and (upper_token in upper_name or upper_name.endswith(upper_token)):
                return row
        return None

    page1: list[dict[str, object]] = []
    for token in ["VRN", "VYN", "VBN"]:
        page1.extend(row for row in [find("[m1]", token), find("[m2]", token)] if row)
    page1.extend(row for row in [find("[m1]", "VOLTAGE-SPARE"), find("[m1]", "IR"), find("[m1]", "IY"), find("[m1]", "IB")] if row)

    page2 = [
        row
        for row in [
            find("[m1]", " IN"),
            find("[m1]", "CURRENT-SPARE"),
            find("[m2]", "VOLTAGE-SPARE"),
            find("[m2]", "IR"),
            find("[m2]", "IY"),
            find("[m2]", "IB"),
            find("[m2]", " IN"),
        ]
        if row
    ]
    page3_analog = [row for row in [find("[m2]", "CURRENT-SPARE")] if row]
    used = {id(row) for row in page1 + page2 + page3_analog}
    leftovers = [row for row in analog_rows if id(row) not in used]
    if leftovers:
        page2.extend(leftovers)

    page3 = page3_analog + digital_rows[:45]
    page4 = digital_rows[45:]
    for row in digital_rows:
        row["compact_digital"] = True
    return [(page1, None, None), (page2, None, None), (page3, None, None), (page4, None, None)]


def _append_calculated_samples(
    root: ET.Element,
    rows: list[dict[str, object]],
    samples: list[list[float]] | None,
) -> list[list[float]] | None:
    if not samples:
        return samples

    calculations = {
        _child_text(calculation, "ChannelNumber"): calculation
        for calculation in _find_all(root, "Calculation")
    }
    if not calculations:
        return samples

    calc_values: dict[str, list[float]] = {}
    analog_values = {str(index): channel for index, channel in enumerate(samples)}

    for number, calculation in calculations.items():
        equation = _child_text(calculation, "Equation")
        values = _calculate_equation(root, equation, analog_values, calc_values)
        if values:
            calc_values[number] = values

    extended = list(samples)
    for row in rows:
        number = str(row.get("number", ""))
        if number in calc_values:
            row["sample_index"] = len(extended)
            extended.append(calc_values[number])
    return extended


def _calculate_equation(
    root: ET.Element,
    equation: str,
    analog_values: dict[str, list[float]],
    calc_values: dict[str, list[float]],
) -> list[float] | None:
    seq_match = re.fullmatch(r'(PositiveSeq|NegativeSeq)\("(-?\d+)","(-?\d+)","(-?\d+)"\)', equation.strip())
    if seq_match:
        kind, a_num, b_num, c_num = seq_match.groups()
        source = {**analog_values, **calc_values}
        try:
            channels = [source[a_num], source[b_num], source[c_num]]
        except KeyError:
            return None
        return _sequence_magnitude(root, channels, negative=(kind == "NegativeSeq"))

    ratio_match = re.fullmatch(r'\("(-?\d+)"/"(-?\d+)"\)\*100', equation.strip())
    if ratio_match:
        numerator, denominator = ratio_match.groups()
        source = {**analog_values, **calc_values}
        if numerator not in source or denominator not in source:
            return None
        count = min(len(source[numerator]), len(source[denominator]))
        values = []
        for index in range(count):
            den = source[denominator][index]
            values.append((source[numerator][index] / den * 100) if den else 0.0)
        return values

    return None


def _sequence_magnitude(root: ET.Element, channels: list[list[float]], negative: bool) -> list[float]:
    rate = _sample_rate(root)
    line_frequency = _line_frequency(root)
    cycle = max(16, int(round(rate / line_frequency)))
    count = min(len(channel) for channel in channels)
    if count < cycle:
        return []

    step = max(1, cycle // 32)
    twiddle = [cmath.exp(-2j * math.pi * index / cycle) for index in range(cycle)]
    phase = cmath.exp(2j * math.pi / 3)
    values: list[float] = []

    last_value = 0.0
    for sample_index in range(count):
        if sample_index % step == 0 or not values:
            start = max(0, min(count - cycle, sample_index - cycle // 2))
            phasors = []
            for channel in channels:
                total = sum(channel[start + index] * twiddle[index] for index in range(cycle))
                phasors.append(total * 2 / cycle / math.sqrt(2))
            if negative:
                sequence = (phasors[0] + phase * phase * phasors[1] + phase * phasors[2]) / 3
            else:
                sequence = (phasors[0] + phase * phasors[1] + phase * phase * phasors[2]) / 3
            last_value = abs(sequence)
        values.append(last_value)
    return values


def _line_frequency(root: ET.Element) -> float:
    try:
        return float(_find_text(root, "LineFrequency"))
    except ValueError:
        return 50.0


def _draw_iq_header(pdf: canvas.Canvas, root: ET.Element, page_width: float, page_height: float, margin: float) -> None:
    y = page_height - 12
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.45)
    pdf.rect(margin, margin - 4, page_width - margin * 2, page_height - margin + 4, stroke=1, fill=0)
    compact_page = page_width <= 500
    pdf.setFont("Helvetica", 5.0 if compact_page else 5.5)
    device_name = _find_text(root, "DeviceName") or _find_text(root, "PathFileName")
    cause = _find_text(root, "CauseOfTrigger") or _find_text(root, "RecordType")
    if _is_merged_cross_trigger_root(root):
        device_name = "MergedDevice(COMTRADE, ID=-1)"
        cause = "FRSENSOR"
    elif cause == "1":
        cause = "FRSENSOR"
        if compact_page:
            device_type = _find_text(root, "DeviceType")
            device_id = _find_text(root, "DeviceID")
            if device_type and device_id:
                device_name = f"{device_name}({device_type}, ID={device_id})"
    fields = [
        ("Substation:", _find_text(root, "StationName")),
        ("Device:", device_name),
        ("COT:", cause),
        ("Record #:", _find_text(root, "RecordNumber")),
        ("Sampling rate:", f"{_find_text(root, 'SampleRateInHz')} Hz"),
    ]
    if compact_page:
        xs = [margin + 2, margin + 72, margin + 185, margin + 252, margin + 303]
    else:
        xs = [margin + 2, margin + 155, margin + 370, margin + 525, page_width - 104]
    value_offsets = [38, 38, 34, 34, 58] if compact_page else [38, 38, 38, 38, 38]
    for x, (label, value), value_width in zip(xs, fields, value_offsets):
        pdf.drawString(x, y, label)
        pdf.drawString(x + value_width, y, _short(value, 35))


def _is_merged_cross_trigger_root(root: ET.Element) -> bool:
    names = [_child_text(channel, "ChannelName") for channel in _find_all(root, "AnalogChannel")]
    return any("[m1]" in name for name in names) and any("[m2]" in name for name in names)


def _draw_time_axis(
    pdf: canvas.Canvas,
    root: ET.Element,
    graph_left: float,
    graph_right: float,
    top: float,
    bottom: float,
    window_start: int | None = None,
    window_end: int | None = None,
) -> None:
    graph_width = graph_right - graph_left
    if _find_text(root, "CauseOfTrigger") == "1" and not _is_merged_cross_trigger_root(root):
        _draw_fault_distance_time_axis(pdf, root, graph_left, graph_width, top, bottom, window_start, window_end)
        return

    pdf.setStrokeColor(colors.HexColor("#bcbcbc"))
    pdf.setLineWidth(0.25)
    for tick in range(11):
        x = graph_left + graph_width * tick / 10
        pdf.line(x, top + 6, x, bottom)
    record_type = _find_text(root, "RecordType").upper()
    pdf.setFont("Helvetica", 5)
    pdf.setFillColor(colors.HexColor("#b5b5b5"))
    if record_type.startswith("DDR"):
        for tick in range(10):
            x = graph_left + graph_width * (tick + 0.1) / 10
            pdf.drawCentredString(x, top + 10, f"{(tick + 1) * 20 % 60:02d}")
    elif _is_merged_cross_trigger_root(root):
        for tick in range(10):
            x = graph_left + graph_width * (tick + 0.1) / 10
            pdf.drawCentredString(x, top + 10, str(350 + tick * 50))
    else:
        for tick in range(10):
            x = graph_left + graph_width * (tick + 0.1) / 10
            pdf.drawCentredString(x, top + 10, str(400 + tick * 50))
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 5.5)
    if record_type.startswith("DDR"):
        start_sample = window_start or int(_find_text(root, "StartSampleIndex") or "0")
        rate = _sample_rate(root)
        label_step = int(round(rate * 100))
        labels = [
            _format_record_time(root, sample_offset=start_sample + label_step * offset, base="start", time_precision="seconds")
            for offset in range(4)
        ]
        xs = [0.0, 0.25, 0.5, 0.75]
        for x_factor, label in zip(xs, labels):
            pdf.drawCentredString(graph_left + graph_width * x_factor, top + 4, label)
        return
    else:
        if _is_merged_cross_trigger_root(root):
            start = _format_record_time(root, offset_seconds=0, base="trigger")
            mid = _format_record_time(root, offset_seconds=0.25, base="trigger")
            end = _format_record_time(root, offset_seconds=0.50, base="trigger")
        else:
            start = _format_record_time(root, offset_seconds=0, base="start")
            mid = _format_record_time(root, offset_seconds=0.25, base="start")
            end = _format_record_time(root, offset_seconds=0.50, base="start")
    pdf.drawCentredString(graph_left + graph_width * 0.15, top + 4, start)
    pdf.drawCentredString(graph_left + graph_width * 0.52, top + 4, mid)
    pdf.drawCentredString(graph_left + graph_width * 0.86, top + 4, end)


def _draw_fault_distance_time_axis(
    pdf: canvas.Canvas,
    root: ET.Element,
    graph_left: float,
    graph_width: float,
    top: float,
    bottom: float,
    window_start: int | None,
    window_end: int | None,
) -> None:
    from datetime import UTC, datetime, timedelta

    rate = _sample_rate(root)
    start_sample = window_start or 0
    end_sample = window_end or int(_find_text(root, "TotalNumberOfSamples") or "0")
    span = max(1, end_sample - start_sample)
    try:
        seconds = float(_find_text(root, "StartTimeSecondsLocal") or _find_text(root, "StartTimeSeconds"))
        micros = int(_find_text(root, "StartTimeMicroSeconds") or "0")
    except ValueError:
        seconds = 0
        micros = 0
    record_start = datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None) + timedelta(microseconds=micros)
    window_dt = record_start + timedelta(seconds=start_sample / rate)
    window_end_dt = record_start + timedelta(seconds=end_sample / rate)
    first_tick = window_dt.replace(microsecond=(window_dt.microsecond // 100000) * 100000)
    if first_tick < window_dt:
        first_tick += timedelta(milliseconds=100)

    pdf.setStrokeColor(colors.HexColor("#bcbcbc"))
    pdf.setLineWidth(0.25)
    pdf.setFont("Helvetica", 5)
    tick = first_tick
    while tick <= window_end_dt:
        offset = (tick - window_dt).total_seconds()
        x = graph_left + graph_width * (offset * rate) / span
        if graph_left <= x <= graph_left + graph_width:
            pdf.setStrokeColor(colors.HexColor("#bcbcbc"))
            pdf.line(x, top + 6, x, bottom)
            ms = tick.microsecond // 1000
            label = tick.strftime("%H:%M:%S.") + f"{ms:03d}" if ms in {0, 500} else f"{ms:03d}"
            pdf.setFillColor(colors.HexColor("#b5b5b5"))
            pdf.drawCentredString(x, top + 10, label)
        tick += timedelta(milliseconds=100)


def _format_iq_marker_time(root: ET.Element, marker_fraction: float, second_offset: int = 0) -> str:
    from datetime import UTC, datetime, timedelta

    try:
        local_seconds = _find_text(root, "TriggerTimeSecondsLocal") or _find_text(root, "StartTimeSecondsLocal")
        raw_seconds = _find_text(root, "TriggerTimeSeconds") or _find_text(root, "StartTimeSeconds")
        seconds = float(local_seconds or raw_seconds)
    except ValueError:
        return ""
    dt = datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None) + timedelta(seconds=second_offset)
    return dt.strftime("%H:%M:%S.") + f"{int(marker_fraction * 1000):03d}"


def _draw_cursors(
    pdf: canvas.Canvas,
    root: ET.Element,
    graph_left: float,
    graph_width: float,
    top: float,
    bottom: float,
    window_start: int | None = None,
    window_end: int | None = None,
) -> None:
    trigger_sample = _trigger_sample_index(root)
    if trigger_sample is not None:
        total = int(float(_find_text(root, "TotalNumberOfSamples") or "0"))
        start = window_start or 0
        end = window_end or total
        span = end - start
        if span > 1 and start <= trigger_sample <= end:
            x = graph_left + graph_width * (trigger_sample - start) / span
            pdf.setStrokeColor(colors.black if _find_text(root, "CauseOfTrigger") == "1" else colors.HexColor("#555555"))
            pdf.setLineWidth(0.45)
            pdf.line(x, top + 10, x, bottom - 2)

    for key, colour, label, dy in [
        ("RedCursorSampleIndex", colors.red, "M", -4),
        ("BlueCursorSampleIndex", colors.blue, "R", -4),
    ]:
        try:
            sample = int(_find_text(root, key))
            total = int(_find_text(root, "TotalNumberOfSamples"))
        except ValueError:
            continue
        start = window_start or 0
        end = window_end or total
        span = end - start
        if span <= 1 or sample < start or sample > end:
            continue
        x = graph_left + graph_width * (sample - start) / span
        pdf.setStrokeColor(colour)
        pdf.setLineWidth(0.35)
        pdf.line(x, top + 10, x, bottom - 2)
        pdf.setFillColor(colour)
        pdf.setFont("Helvetica", 6)
        pdf.drawCentredString(x, bottom + dy, label)


def _draw_iq_footer(
    pdf: canvas.Canvas,
    root: ET.Element,
    page_width: float,
    margin: float,
    page_number: int,
    page_count: int,
    page_label: tuple[str, str] | None = None,
) -> None:
    pdf.setFillColor(colors.black)
    cursor_base = "start"
    tm = _format_record_time(root, sample_key="RedCursorSampleIndex", base=cursor_base, include_millis=True).split(" ", 1)[-1]
    tr = _format_record_time(root, sample_key="BlueCursorSampleIndex", base=cursor_base, include_millis=True).split(" ", 1)[-1]
    if page_width <= 500:
        pdf.setFont("Helvetica", 4.8)
        pdf.drawString(margin + 2, margin - 1, f"TT:     {_format_record_time(root, include_millis=True)}")
        pdf.drawString(margin + 112, margin - 1, f"TM:     {tm}")
        pdf.drawString(margin + 188, margin - 1, f"TR:     {tr}")
        pdf.drawString(margin + 264, margin - 1, f"TM-TR:     {_cursor_delta(root)}")
    else:
        pdf.setFont("Helvetica", 5.5)
        pdf.drawString(margin + 2, margin - 1, f"TT:     {_format_record_time(root, include_millis=True)}")
        pdf.drawString(margin + 210, margin - 1, f"TM:     {tm}")
        pdf.drawString(margin + 390, margin - 1, f"TR:     {tr}")
        pdf.drawString(margin + 560, margin - 1, f"TM-TR:     {_cursor_delta(root)}")
    if page_label:
        pdf.drawRightString(page_width - margin - 2, margin - 1, f"Page {page_label[0]} of {page_label[1]}")
    else:
        pdf.drawRightString(page_width - margin - 2, margin - 1, f"Page {page_number} of {page_count}")


def _format_record_time(
    root: ET.Element,
    offset_seconds: float = 0,
    sample_key: str | None = None,
    sample_offset: float | None = None,
    base: str = "trigger",
    include_millis: bool = False,
    time_precision: str = "millis",
) -> str:
    from datetime import UTC, datetime, timedelta

    try:
        if base == "start":
            local_seconds = _find_text(root, "StartTimeSecondsLocal")
            raw_seconds = _find_text(root, "StartTimeSeconds")
            micros = int(_find_text(root, "StartTimeMicroSeconds") or "0")
        else:
            local_seconds = _find_text(root, "TriggerTimeSecondsLocal") or _find_text(root, "StartTimeSecondsLocal")
            raw_seconds = _find_text(root, "TriggerTimeSeconds") or _find_text(root, "StartTimeSeconds")
            micros = int(_find_text(root, "TriggerTimeMicroSeconds") or "0")
        if local_seconds and float(local_seconds) != 0:
            seconds = float(local_seconds)
            use_utc_display = True
        else:
            seconds = float(raw_seconds)
            use_utc_display = False
    except ValueError:
        return ""

    if sample_key:
        try:
            sample = int(_find_text(root, sample_key))
            rate = float(_find_text(root, "SampleRateInHz"))
            offset_seconds = sample / rate
        except ValueError:
            offset_seconds = 0
    if sample_offset is not None:
        offset_seconds = sample_offset / _sample_rate(root)

    if use_utc_display:
        dt = datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
    else:
        dt = datetime.fromtimestamp(seconds)
    dt = dt + timedelta(microseconds=micros, seconds=offset_seconds)
    if include_millis:
        return dt.strftime("%d/%m/%Y %H:%M:%S.") + f"{dt.microsecond // 1000:03d}.{dt.microsecond % 1000:03d}"
    if time_precision == "seconds":
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _trigger_sample_index(root: ET.Element) -> int | None:
    try:
        start_seconds = float(_find_text(root, "StartTimeSeconds"))
        trigger_seconds = float(_find_text(root, "TriggerTimeSeconds") or start_seconds)
        start_microseconds = float(_find_text(root, "StartTimeMicroSeconds") or "0")
        trigger_microseconds = float(_find_text(root, "TriggerTimeMicroSeconds") or "0")
        sample = ((trigger_seconds - start_seconds) + (trigger_microseconds - start_microseconds) / 1_000_000) * _sample_rate(root)
    except ValueError:
        return None
    return int(round(sample))


def _cursor_delta(root: ET.Element) -> str:
    try:
        red = int(_find_text(root, "RedCursorSampleIndex"))
        blue = int(_find_text(root, "BlueCursorSampleIndex"))
    except ValueError:
        return "-00:00:00.000.000"
    seconds = (red - blue) / _sample_rate(root)
    sign = "-" if seconds < 0 else ""
    total_us = int(round(abs(seconds) * 1_000_000))
    hours, rem = divmod(total_us, 3_600_000_000)
    minutes, rem = divmod(rem, 60_000_000)
    whole, rem = divmod(rem, 1_000_000)
    millis, micros = divmod(rem, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{whole:02d}.{millis:03d}.{micros:03d}"


def _sample_rate(root: ET.Element) -> float:
    try:
        return float(_find_text(root, "SampleRateInHz"))
    except ValueError:
        return 1.0


def _cursor_value(
    samples: list[list[float]] | None,
    channel_index: int,
    root: ET.Element,
    key: str,
    row: dict[str, object],
) -> str:
    if not samples or channel_index < 0 or channel_index >= len(samples):
        return ""
    try:
        sample = int(_find_text(root, key))
        if str(row.get("cursor_value_style", "")).lower() == "primaryrms":
            value = _cursor_rms_value(samples[channel_index], sample, _sample_rate(root), row)
            return f"{value:.2f}R"
        value = samples[channel_index][sample]
    except (ValueError, IndexError):
        return ""
    return _display_value(row, value)


def _cursor_rms_value(values: list[float], sample: int, rate: float, row: dict[str, object]) -> float:
    window = max(1, int(round(rate / 50)))
    start = max(0, sample - window)
    segment = values[start:sample] or values[max(0, sample - window // 2) : min(len(values), sample + window // 2)]
    if not segment:
        return 0.0
    rms = math.sqrt(sum(float(value) * float(value) for value in segment) / len(segment))
    unit = str(row.get("unit", "")).lower()
    if unit == "a":
        if str(row.get("name", "")).startswith("[m"):
            return rms
        return rms * 1000
    if unit == "v":
        return rms
    return rms


def _display_value(row: dict[str, object], value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _short(value, 8)
    number /= _display_scale(row)
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 10:
        return f"{number:.2f}"
    return f"{number:.2f}"


def _display_scale(row: dict[str, object]) -> float:
    unit = str(row.get("unit", "")).lower()
    if unit not in {"v", "a"}:
        return 1
    if row.get("fault_distance"):
        return 1000
    if str(row.get("name", "")).startswith("[m") and unit == "a":
        return 1
    if str(row.get("cursor_value_style", "")).lower() in {"instantaneous", "primaryrms"} and unit == "a":
        return 0.001
    if str(row.get("record_type", "")).startswith("DDR") and unit == "a":
        return 0.001
    try:
        biggest = max(abs(float(row["min"])), abs(float(row["max"])))
    except (TypeError, ValueError):
        return 1
    return 1000 if biggest >= 1000 else 1


def _display_channel_name(row: dict[str, object]) -> str:
    name = str(row["name"])
    if str(row.get("cursor_value_style", "")).lower() in {"primaryrms", "instantaneous"}:
        unit = _display_unit(row, for_name=True)
        if unit:
            name = f"{name} ({unit})"
    return _short(name, 38)


def _display_unit(row: dict[str, object], for_name: bool = False) -> str:
    cursor_style = str(row.get("cursor_value_style", "")).lower()
    if not for_name and cursor_style in {"primaryrms", "instantaneous"}:
        return ""
    unit = str(row["unit"])
    lower = unit.lower()
    if lower == "v":
        if row.get("fault_distance"):
            return "kV"
        if for_name and cursor_style in {"primaryrms", "instantaneous"}:
            return "V"
        return "kV" if _display_scale(row) == 1000 else "V"
    if lower == "a":
        if row.get("fault_distance"):
            return "kA"
        if for_name and cursor_style in {"primaryrms", "instantaneous"}:
            if str(row.get("name", "")).startswith("[m"):
                return "A"
            return "mA"
        scale = _display_scale(row)
        if scale == 0.001:
            return "mA"
        return "kA" if scale == 1000 else "A"
    return unit


def _synthetic_points(
    index: int,
    row: dict[str, object],
    graph_left: float,
    graph_width: float,
    y_mid: float,
    row_height: float,
) -> list[tuple[float, float]]:
    if row.get("digital"):
        y = y_mid
        return [(graph_left, y), (graph_left + graph_width, y)]

    if row.get("calculated") or str(row.get("unit", "")) == "%":
        try:
            y_min = float(row["min"])
            y_max = float(row["max"])
        except (TypeError, ValueError):
            y_min, y_max = 0.0, 1.0
        if y_max == y_min:
            y_max = y_min + 1
        top = y_mid + row_height * 0.34
        bottom = y_mid - row_height * 0.34
        points = []
        for i in range(360):
            t = i / 359
            value = (y_min + y_max) / 2
            value += (y_max - y_min) * 0.20 * sin(2 * pi * 2.6 * t + index)
            value += (y_max - y_min) * 0.08 * sin(2 * pi * 17 * t)
            y = bottom + ((value - y_min) / (y_max - y_min)) * (top - bottom)
            points.append((graph_left + graph_width * t, y))
        return points

    phase = {"A": 0, "B": -2 * pi / 3, "C": 2 * pi / 3}.get(str(row["phase"]), index * 0.35)
    cycles = 8
    amp = row_height * 0.34
    points = []
    if row["ac"]:
        for i in range(700):
            t = i / 699
            y = y_mid + amp * sin(2 * pi * cycles * t + phase)
            points.append((graph_left + graph_width * t, y))
    else:
        for i in range(180):
            t = i / 179
            y = y_mid + sin(2 * pi * 2 * t + phase) * amp * 0.08
            points.append((graph_left + graph_width * t, y))
    return points


def _row_weight(row: dict[str, object]) -> float:
    if row.get("digital"):
        if row.get("compact_digital"):
            return 0.09
        return 0.14
    return 1.0


def _sample_points(
    samples: list[list[float]] | None,
    index: int,
    row: dict[str, object],
    graph_left: float,
    graph_width: float,
    y_mid: float,
    row_height: float,
    window_start: int | None = None,
    window_end: int | None = None,
) -> list[tuple[float, float]]:
    if not samples or index < 0 or index >= len(samples) or not samples[index]:
        return []

    values = samples[index]
    if window_start is not None or window_end is not None:
        values = values[window_start or 0 : window_end or len(values)]
    if not values:
        return []
    max_points = 6000 if str(row.get("record_type", "")).startswith("DDR") else 1800
    step = max(1, len(values) // max_points)
    sampled = values[::step]
    try:
        y_min = float(row["min"])
        y_max = float(row["max"])
    except (TypeError, ValueError):
        y_min = min(sampled)
        y_max = max(sampled)
    if y_max == y_min:
        y_min, y_max = min(sampled), max(sampled)
    if y_max == y_min:
        return []

    top = y_mid + row_height * 0.38
    bottom = y_mid - row_height * 0.38
    points = []
    last = len(sampled) - 1
    for point_index, value in enumerate(sampled):
        t = point_index / last if last else 0
        clipped = max(y_min, min(y_max, value))
        y = bottom + ((clipped - y_min) / (y_max - y_min)) * (top - bottom)
        points.append((graph_left + graph_width * t, y))
    return points


def _channel_rows(root: ET.Element, analog_channels: list[ET.Element], bands: list[ET.Element]) -> list[dict[str, object]]:
    record_type = _find_text(root, "RecordType").upper()
    cursor_value_style = _find_text(root, "CursorValueStyle")
    fault_distance = _find_text(root, "RecordType").upper() == "DFR" and _find_text(root, "CauseOfTrigger") == "1" and not _is_merged_cross_trigger_root(root)
    band_by_number = {}
    band_channels = []
    for band in bands:
        channel = next((node for node in band.iter() if _local_name(node.tag) == "Channel"), None)
        if channel is not None:
            band_channels.append(channel)
            if _bool_child(channel, "ACChannel", default=True) or _child_text(channel, "Number").startswith("-"):
                band_by_number[_child_text(channel, "Number")] = channel

    rows = []
    analog_numbers = set()
    for sample_index, channel in enumerate(analog_channels):
        number = _child_text(channel, "ChannelNumber")
        analog_numbers.add(number)
        band_channel = band_by_number.get(number)
        rows.append(
            {
                "number": number,
                "sample_index": sample_index,
                "name": _child_text(channel, "ChannelName"),
                "phase": _child_text(channel, "Phase-Label"),
                "unit": _child_text(channel, "ChannelUnit"),
                "min": _child_text(channel, "MinValue"),
                "max": _child_text(channel, "MaxValue"),
                "record_type": record_type,
                "cursor_value_style": cursor_value_style,
                "fault_distance": fault_distance,
                "ac": _bool_child(band_channel, "ACChannel", default=True) if band_channel is not None else True,
                "visible": _bool_child(band_channel, "IsVisible", default=True) if band_channel is not None else True,
                "colour": _reportlab_colour(_child_text(band_channel, "BackgroundColor") if band_channel is not None else ""),
            }
        )

    for band_channel in band_channels:
        number = _child_text(band_channel, "Number")
        if number.startswith("-"):
            continue
        if _bool_child(band_channel, "ACChannel", default=True):
            continue
        if not _bool_child(band_channel, "IsVisible", default=False):
            continue
        rows.append(
            {
                "number": number,
                "sample_index": -1,
                "name": _child_text(band_channel, "Name"),
                "phase": "",
                "unit": "",
                "min": "0",
                "max": "1",
                "record_type": record_type,
                "cursor_value_style": cursor_value_style,
                "fault_distance": fault_distance,
                "ac": False,
                "visible": True,
                "digital": True,
                "colour": _reportlab_colour(_child_text(band_channel, "BackgroundColor")),
            }
        )

    calculations = {
        _child_text(calculation, "ChannelNumber"): calculation
        for calculation in _find_all(root, "Calculation")
    }
    for number, band_channel in band_by_number.items():
        if number in analog_numbers or not number.startswith("-"):
            continue
        calculation = calculations.get(number)
        rows.append(
            {
                "number": number,
                "sample_index": -1,
                "name": _child_text(band_channel, "Name"),
                "phase": "",
                "unit": _child_text(calculation, "ChannelUnit") if calculation is not None else "",
                "min": _child_text(band_channel, "GraticuleMinValue") or (_child_text(calculation, "MinValue") if calculation is not None else ""),
                "max": _child_text(band_channel, "GraticuleMaxValue") or (_child_text(calculation, "MaxValue") if calculation is not None else ""),
                "record_type": record_type,
                "ac": False,
                "visible": True,
                "calculated": True,
                "colour": _reportlab_colour(_child_text(band_channel, "BackgroundColor")),
            }
        )
    return rows


def _bool_child(root: ET.Element | None, name: str, default: bool = False) -> bool:
    if root is None:
        return default
    value = _child_text(root, name).lower()
    return default if not value else value == "true"


def _reportlab_colour(value: str):
    try:
        signed = int(value)
    except (TypeError, ValueError):
        return colors.HexColor("#2563eb")
    unsigned = signed & 0xFFFFFF
    red = (unsigned >> 16) & 255
    green = (unsigned >> 8) & 255
    blue = unsigned & 255
    return colors.Color(red / 255, green / 255, blue / 255)


def _find_text(root: ET.Element, name: str) -> str:
    match = next((node for node in root.iter() if _local_name(node.tag) == name), None)
    return (match.text or "").strip() if match is not None else ""


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in root.iter() if _local_name(node.tag) == name]


def _child_text(root: ET.Element, name: str) -> str:
    match = next((node for node in root if _local_name(node.tag) == name), None)
    return (match.text or "").strip() if match is not None else ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _table(rows: list[list[str]]) -> Table:
    styles = getSampleStyleSheet()
    safe_rows = [[Paragraph(_escape(_short(cell)), styles["BodyText"]) for cell in row] for row in rows]
    table = Table(safe_rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _escape(value: object) -> str:
    return html.escape(str(value))


def _short(value: object, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ConverterApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("CDF to PDF Converter")
        self.root.geometry("680x380")
        self.root.minsize(620, 320)

        self.input_file = StringVar()
        self.output_file = StringVar()
        self.status = StringVar(value="Choose one or more CDF files to begin.")
        self.input_files: list[str] = []

        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(1, weight=1)

        ttk.Label(main, text="CDF files").grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=8)
        self.files_list = Listbox(main, selectmode=MULTIPLE, height=7)
        self.files_list.grid(row=0, column=1, rowspan=2, sticky="nsew", pady=8)
        ttk.Button(main, text="Browse", command=self.choose_input).grid(row=0, column=2, sticky="n", padx=(10, 0), pady=8)
        ttk.Button(main, text="Clear", command=self.clear_inputs).grid(row=1, column=2, sticky="n", padx=(10, 0), pady=8)

        ttk.Label(main, text="Output folder").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=8)
        ttk.Entry(main, textvariable=self.output_file).grid(row=2, column=1, sticky="ew", pady=8)
        ttk.Button(main, text="Choose", command=self.choose_output).grid(row=2, column=2, padx=(10, 0), pady=8)

        ttk.Button(main, text="Convert to PDF", command=self.convert).grid(
            row=3, column=1, sticky="e", pady=(18, 8)
        )
        ttk.Label(main, textvariable=self.status, wraplength=500).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0)
        )

    def choose_input(self) -> None:
        selected = filedialog.askopenfilenames(filetypes=[("CDF files", "*.cdf"), ("All files", "*.*")])
        if not selected:
            return
        self.input_files = list(selected)
        self.files_list.delete(0, END)
        for file_name in self.input_files:
            self.files_list.insert(END, file_name)
        if not self.output_file.get():
            self.output_file.set(str(Path(self.input_files[0]).parent))

    def choose_output(self) -> None:
        selected = filedialog.askdirectory()
        if selected:
            self.output_file.set(selected)

    def clear_inputs(self) -> None:
        self.input_files = []
        self.files_list.delete(0, END)
        self.status.set("Choose one or more CDF files to begin.")

    def convert(self) -> None:
        if not self.input_files:
            messagebox.showerror("No files selected", "Choose one or more CDF files first.")
            return
        output_folder = Path(self.output_file.get() or Path(self.input_files[0]).parent)
        failures = []
        converted = 0
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            for file_name in self.input_files:
                input_path = Path(file_name)
                output_path = output_folder / input_path.with_suffix(".pdf").name
                try:
                    convert_cdf_to_pdf(input_path, output_path)
                    converted += 1
                except Exception as exc:
                    failures.append(f"{input_path.name}: {exc}")
        finally:
            self.root.config(cursor="")

        if failures:
            self.status.set(f"Converted {converted} file(s). {len(failures)} failed.")
            messagebox.showerror("Batch complete with errors", "\n\n".join(failures[:5]))
        else:
            self.status.set(f"Converted {converted} file(s) to {output_folder}")
            messagebox.showinfo("Conversion complete", f"Converted {converted} file(s).\n\nSaved to:\n{output_folder}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert CDF files to PDF.")
    parser.add_argument("paths", nargs="*", help="Input .cdf file(s), or one input plus one output .pdf file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output PDF file for one input, or output folder for multiple inputs.",
    )
    args = parser.parse_args()

    if args.paths:
        try:
            converted = _convert_cli_paths([Path(path) for path in args.paths], Path(args.output) if args.output else None)
        except Exception as exc:
            print(f"Conversion failed: {exc}", file=sys.stderr)
            if sys.stdout is None or sys.stderr is None:
                try:
                    messagebox.showerror("Conversion failed", str(exc))
                except Exception:
                    pass
            return 1
        message = "\n".join(f"{source} -> {target}" for source, target in converted)
        print(f"Converted {len(converted)} file(s):\n{message}")
        if sys.stdout is None or sys.stderr is None:
            try:
                messagebox.showinfo("Conversion complete", f"Converted {len(converted)} file(s).\n\n{message}")
            except Exception:
                pass
        return 0

    ConverterApp().run()
    return 0


def _convert_cli_paths(paths: list[Path], output: Path | None) -> list[tuple[Path, Path]]:
    if len(paths) == 2 and paths[0].suffix.lower() == ".cdf" and paths[1].suffix.lower() == ".pdf" and output is None:
        output_path = paths[1]
        convert_cdf_to_pdf(paths[0], output_path)
        return [(paths[0], output_path)]

    inputs = paths
    for input_path in inputs:
        if input_path.suffix.lower() != ".cdf":
            raise ConversionError(f"Expected a .cdf input file, got: {input_path}")

    output_folder = output
    single_output_pdf = output is not None and output.suffix.lower() == ".pdf"
    if single_output_pdf and len(inputs) != 1:
        raise ConversionError("--output can be a PDF file only when one input CDF is supplied.")
    if output_folder is None or single_output_pdf:
        output_folder = inputs[0].parent

    converted = []
    for input_path in inputs:
        output_path = output if single_output_pdf else output_folder / input_path.with_suffix(".pdf").name
        convert_cdf_to_pdf(input_path, output_path)
        converted.append((input_path, output_path))
    return converted


if __name__ == "__main__":
    raise SystemExit(main())
