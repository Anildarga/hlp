from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import streamlit as st

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from common.auth.machine_lock import is_authorized
from common.auth.user_store import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    add_user,
    authenticate_user,
    delete_user,
    list_users,
)
from models.hv.gui.constants import (
    CATEGORY_OPTIONS,
    VARIANT_OPTIONS,
    VARIANT_PARAMS,
    format_pack_qr_data,
    get_type_of_reess,
    variants_for_category,
)
from models.hv.nomenclature.bms_id import short_bmb_cmb_id
from models.hv.nomenclature.module_id import parse_module_id
from models.hv.nomenclature.scan_handler import ScanType, identify_scan
from models.hv.nomenclature.serial_number import build_serial_number, parse_serial_number
from models.hv.printing.constants import PRINTER_BMS_ID, PRINTER_DUMMY, PRINTER_PACK_ID
from models.hv.printing.labels import (
    build_battery_pack_id_label,
    build_bmb_cmb_combined_label,
    build_pack_qr_label,
)
from models.hv.printing.spooler import send_zpl
from models.hv.session.bms_scan_log import (
    BmsScanRecord,
    append_scan_record as append_bms_record,
    find_record_by_pack_qr,
    load_all_records as load_bms_records,
    load_all_records_with_rows as load_bms_records_with_rows,
    load_rework_records,
    open_log_file as open_bms_log,
    patch_rework,
)
from models.hv.session.pack_id_scan_log import (
    PackIdScanRecord,
    append_scan_record as append_pack_id_record,
    load_all_records as load_pack_id_records,
    open_log_file as open_pack_id_log,
)
from models.hv.session.pack_store import (
    increment_pack_serial,
    load_pack_serial,
    load_pack_serial_count,
    save_pack_serial,
    save_pack_serial_count,
)
from models.hv.session.scan_log import (
    ScanRecord,
    append_scan_record,
    load_all_records as load_dummy_records,
    load_all_records_with_rows,
    load_unacknowledged_issues,
    open_log_file as open_dummy_log,
    patch_admin_ack,
)

st.set_page_config(page_title="HV Label Printer", layout="wide")

_DEFAULTS = {
    "username": None,
    "role": None,
    "excel_password": None,
    "print_mode": "Print",
    "dummy_modules": ["", "", "", ""],
    "dummy_pending": False,
    "dummy_pending_config": {},
    "dummy_initialized": False,
    "bms_pack_qr": "",
    "bms_pack_date": "",
    "bms_bmb": "",
    "bms_cmb": "",
    "bms_rework_bmb": "",
    "bms_rework_cmb": "",
    "bms_existing": None,
    "bms_rework": False,
    "manual_serial": "",
    "auth_notice_shown": False,
}


def _init() -> None:
    for key, value in _DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _capture(key: str) -> None:
    st.session_state[f"{key}_submitted"] = st.session_state.get(key, "")
    st.session_state[key] = ""


def _take(key: str) -> str:
    value = st.session_state.pop(f"{key}_submitted", "")
    return value.strip() if isinstance(value, str) else ""


def _password() -> str:
    return st.session_state.get("excel_password") or "06082003"


def _show_zpl(zpl: str, filename: str, printer: str, key: str) -> bool:
    if st.session_state.print_mode == "Save ZPL":
        st.download_button(
            "Download ZPL",
            zpl.encode("utf-8"),
            file_name=filename,
            mime="text/plain",
            key=f"download_{key}",
        )
        return True
    try:
        send_zpl(zpl, printer)
    except (OSError, ValueError) as exc:
        st.error(f"Printing failed: {exc}")
        return False
    st.success("Label sent to the printer.")
    return True


def _init_dummy() -> None:
    for key, value in st.session_state.pop("dummy_pending_config", {}).items():
        st.session_state[key] = value
    parsed = None
    if any(
        key not in st.session_state
        for key in (
            "dummy_plant",
            "dummy_line",
            "dummy_variant",
            "dummy_category",
            "dummy_shift",
            "dummy_date",
            "dummy_count",
        )
    ):
        parsed = parse_serial_number(load_pack_serial())
    now = datetime.now()
    shift = "A" if 6 <= now.hour < 15 else "B" if 15 <= now.hour < 23 else "C"
    defaults = {
        "dummy_plant": parsed.plant if parsed else "Jigani",
        "dummy_line": parsed.line if parsed else "1",
        "dummy_variant": parsed.variant if parsed and parsed.variant else VARIANT_OPTIONS[-1],
        "dummy_category": (
            parsed.category.title() if parsed and parsed.category else "Domestic"
        ),
        "dummy_shift": parsed.shift if parsed and parsed.shift else shift,
        "dummy_date": parsed.as_date if parsed else date.today(),
        "dummy_count": (
            parsed.serial_count if parsed else load_pack_serial_count() or "00001"
        ),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    st.session_state.dummy_initialized = True


def _dummy_serial() -> str:
    count = str(st.session_state.dummy_count).strip()
    if not count.isdigit():
        raise ValueError("Serial count must contain digits only.")
    count = str(max(1, min(99999, int(count)))).zfill(5)
    return build_serial_number(
        plant=st.session_state.dummy_plant,
        line=st.session_state.dummy_line,
        when=st.session_state.dummy_date,
        serial_count=count,
        model="high voltage",
        variant=st.session_state.dummy_variant,
        shift=st.session_state.dummy_shift,
        category=st.session_state.dummy_category,
    )


@st.fragment(run_every="1s")
def _dummy_clock() -> None:
    st.text_input(
        "Time",
        value=datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
        disabled=True,
    )


def _dummy_page() -> None:
    st.header("Battery Pack Dummy ID")
    _init_dummy()
    admin = st.session_state.role in (ROLE_ADMIN, ROLE_OPERATOR)
    categories = CATEGORY_OPTIONS
    allowed = variants_for_category(st.session_state.dummy_category)
    if st.session_state.dummy_variant not in allowed:
        st.session_state.dummy_variant = allowed[0]
    with st.form("dummy_config"):
        cols = st.columns(4)
        cols[0].selectbox("Category", categories, key="dummy_category", disabled=not admin)
        cols[1].selectbox("Plant", ("Jigani", "Domlur"), key="dummy_plant", disabled=not admin)
        cols[2].selectbox("Line", ("1", "2", "3"), key="dummy_line", disabled=not admin)
        cols[3].selectbox("Variant", allowed, key="dummy_variant", disabled=not admin)
        cols = st.columns(2)
        cols[0].selectbox("Shift", ("A", "B", "C"), key="dummy_shift", disabled=True)
        cols[1].text_input("Serial count", key="dummy_count", disabled=not admin)
        apply = st.form_submit_button("Apply configuration")
    cols = st.columns(2)
    display_date = (
        str(st.session_state.dummy_date.year)
        if st.session_state.dummy_category == "Export"
        else st.session_state.dummy_date.strftime("%d %b %Y")
    )
    cols[0].text_input("Date", value=display_date, disabled=True)
    with cols[1]:
        _dummy_clock()
    try:
        serial = _dummy_serial()
    except (ValueError, KeyError) as exc:
        st.error(f"Invalid configuration: {exc}")
        return
    if apply:
        save_pack_serial(serial)
        save_pack_serial_count(st.session_state.dummy_count)
        st.success(f"Pack serial: {serial}")
    params = VARIANT_PARAMS[st.session_state.dummy_variant]
    st.caption(
        f"Current serial: `{serial}` · {params['rated_capacity']} · {params['max_voltage']}"
    )
    st.subheader("Scan module IDs")
    columns = st.columns(4)
    for index, value in enumerate(st.session_state.dummy_modules):
        columns[index].metric(f"M{index + 1}", "Scanned" if value else "Waiting")
        if value:
            columns[index].code(value)
    with st.form("dummy_scan"):
        st.text_input("Scanner", key="dummy_scan_input", placeholder="Scan M1–M4")
        st.form_submit_button("Process scan", on_click=_capture, args=("dummy_scan_input",))
    raw = _take("dummy_scan_input")
    if raw:
        result = identify_scan(raw)
        if result.scan_type != ScanType.MODULE_ID or result.module is None:
            st.warning("Scan a valid module ID QR code.")
        else:
            modules = list(st.session_state.dummy_modules)
            duplicate = next((i for i, value in enumerate(modules) if value == result.raw), None)
            slot = next((i for i, value in enumerate(modules) if not value), None)
            first = parse_module_id(modules[0]) if modules[0] else None
            module = result.module
            if duplicate is not None:
                st.info(f"This module is already in M{duplicate + 1}.")
            elif slot is None:
                st.warning("All four modules are scanned. Clear them before rescanning.")
            elif st.session_state.dummy_category == "Export" and module.variant not in variants_for_category("Export"):
                st.error(f"Export does not support {module.variant} modules.")
            elif first and (module.variant != first.variant or module.plant != first.plant):
                st.error("All four modules must have the same variant and plant.")
            else:
                modules[slot] = result.raw
                st.session_state.dummy_modules = modules
                st.session_state.dummy_pending_config = {
                    "dummy_variant": module.variant,
                    "dummy_plant": module.plant,
                }
                st.session_state.dummy_pending = all(modules)
                st.rerun()
    if st.button("Clear modules", key="dummy_clear"):
        st.session_state.dummy_modules = ["", "", "", ""]
        st.session_state.dummy_pending = False
        st.rerun()
    if not st.session_state.dummy_pending:
        return
    modules = [parse_module_id(value) for value in st.session_state.dummy_modules]
    if len({item.part_number for item in modules if item}) != 1:
        st.error("All modules must use the same part number.")
        return
    serial = _dummy_serial()
    variant = st.session_state.dummy_variant
    pack_qr = format_pack_qr_data(variant=variant, serial_number=serial)
    st.success("INTEGRATION OK")
    st.code(pack_qr)
    with st.form("dummy_complete"):
        ref_no = st.text_input("Ref No")
        complete = st.form_submit_button("Print and complete pack")
    if complete:
        _complete_dummy(serial, pack_qr, ref_no.strip())


def _complete_dummy(serial: str, pack_qr: str, ref_no: str) -> None:
    parsed = parse_serial_number(serial)
    variant = st.session_state.dummy_variant
    params = VARIANT_PARAMS[variant]
    record = ScanRecord(
        date=parsed.format_date() if parsed else "",
        time=datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
        serial_count=parsed.serial_count if parsed else "",
        ref_no=ref_no,
        variant=variant,
        m1=st.session_state.dummy_modules[0],
        m2=st.session_state.dummy_modules[1],
        m3=st.session_state.dummy_modules[2],
        m4=st.session_state.dummy_modules[3],
        dummy_pack_status="INTEGRATION OK",
        dummy_pack_qr=pack_qr,
        serial_number=serial,
    )
    try:
        append_scan_record(record, excel_password=_password())
    except Exception as exc:
        st.error(f"Could not write scan log: {exc}")
        return
    zpl = build_pack_qr_label(
        pack_qr_data=pack_qr,
        model_number=get_type_of_reess(variant),
        serial_number=serial,
        tac_number=params["tac_number"],
        mfg_date=parsed.format_date() if parsed else "",
        variant=variant,
        category=st.session_state.dummy_category,
    )
    if not _show_zpl(zpl, f"pack_{serial}.zpl", PRINTER_DUMMY, "dummy"):
        return
    increment_pack_serial()
    st.session_state.dummy_count = load_pack_serial_count() or st.session_state.dummy_count
    st.session_state.dummy_modules = ["", "", "", ""]
    st.session_state.dummy_pending = False
    st.success("Pack completed; ready for the next set of modules.")


def _pack_id_page() -> None:
    st.header("Battery Pack ID")
    st.selectbox("Category", CATEGORY_OPTIONS, key="pack_category")
    st.selectbox(
        "Variant",
        VARIANT_OPTIONS,
        key="pack_variant",
        disabled=st.session_state.role not in (ROLE_ADMIN, ROLE_OPERATOR),
    )
    with st.form("pack_id_scan"):
        st.text_input("Scanner", key="pack_scan_input", placeholder="Scan pack QR")
        st.form_submit_button("Process scan", on_click=_capture, args=("pack_scan_input",))
    raw = _take("pack_scan_input")
    if not raw:
        return
    result = identify_scan(raw)
    if result.scan_type != ScanType.SERIAL_NUMBER or result.serial is None or not result.pack_variant:
        st.warning("Scan a valid full pack QR code.")
        return
    variant = result.pack_variant
    params = VARIANT_PARAMS[variant]
    serial = result.serial.raw
    pack_qr = result.raw
    mfg_date = result.serial.format_date()
    zpl = build_battery_pack_id_label(
        serial_number=serial,
        rated_capacity=params["rated_capacity"],
        max_voltage=params["max_voltage"],
        model_number=get_type_of_reess(variant),
        tac_number=params["tac_number"],
        variant=variant,
        category=st.session_state.pack_category,
        mfg_date=mfg_date,
        pack_qr_data=pack_qr,
    )
    if not _show_zpl(zpl, f"pack_{serial}.zpl", PRINTER_PACK_ID, "pack_id"):
        return
    save_pack_serial(serial)
    try:
        append_pack_id_record(
            PackIdScanRecord(
                date=mfg_date,
                time=datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
                serial_number=serial,
                variant=variant,
                pack_qr_data=pack_qr,
            ),
            excel_password=_password(),
        )
    except Exception as exc:
        st.error(f"Label generated, but scan log failed: {exc}")


def _reset_bms() -> None:
    for key in (
        "bms_pack_qr", "bms_pack_date", "bms_bmb", "bms_cmb",
        "bms_rework_bmb", "bms_rework_cmb",
    ):
        st.session_state[key] = ""
    st.session_state.bms_existing = None


def _bms_page() -> None:
    st.header("BMB / CMB ID")
    st.checkbox("Rework existing pack", key="bms_rework")
    if st.session_state.bms_pack_qr:
        st.write(f"Pack QR: `{st.session_state.bms_pack_qr}`")
        st.write(f"BMB ID: `{st.session_state.bms_bmb or 'Waiting'}`")
        st.write(f"CMB ID: `{st.session_state.bms_cmb or 'Waiting'}`")
        if st.session_state.bms_rework:
            st.write(f"Rework BMB: `{st.session_state.bms_rework_bmb or '—'}`")
            st.write(f"Rework CMB: `{st.session_state.bms_rework_cmb or '—'}`")
    with st.form("bms_scan"):
        st.text_input("Scanner", key="bms_scan_input", placeholder="Scan pack QR, BMB, or CMB")
        st.form_submit_button("Process scan", on_click=_capture, args=("bms_scan_input",))
    raw = _take("bms_scan_input")
    if not raw:
        if st.button("Clear pack", key="bms_clear"):
            _reset_bms()
            st.rerun()
        return
    result = identify_scan(raw)
    if result.scan_type == ScanType.SERIAL_NUMBER and result.serial:
        if not result.pack_variant:
            st.warning("Scan the full pack QR.")
            return
        _reset_bms()
        st.session_state.bms_pack_qr = result.raw
        st.session_state.bms_pack_date = result.serial.format_date()
        if st.session_state.bms_rework:
            record = find_record_by_pack_qr(result.raw)
            if record is None:
                _reset_bms()
                st.warning("No saved BMB/CMB record for that pack.")
                return
            st.session_state.bms_existing = record
            st.session_state.bms_bmb = record.bmb_id
            st.session_state.bms_cmb = record.cmb_id
            st.session_state.bms_rework_bmb = record.rework_bmb_id
            st.session_state.bms_rework_cmb = record.rework_cmb_id
        st.success("Pack QR loaded. Scan the BMB and CMB IDs.")
        st.rerun()
    if result.scan_type not in (ScanType.BMB_ID, ScanType.CMB_ID) or result.bmb_cmb is None:
        st.warning("Scan a pack QR, BMB ID, or CMB ID.")
        return
    if not st.session_state.bms_pack_qr:
        st.warning("Scan the pack QR first.")
        return
    kind = result.bmb_cmb.kind
    if st.session_state.bms_rework:
        _bms_rework(kind, result.bmb_cmb.raw)
        return
    if kind == "BMB":
        st.session_state.bms_bmb = result.bmb_cmb.raw
    else:
        st.session_state.bms_cmb = result.bmb_cmb.raw
    if st.session_state.bms_bmb and st.session_state.bms_cmb:
        _bms_complete()
    else:
        st.success(f"{kind} accepted. Scan the other ID.")
        st.rerun()


def _print_bms_pair(bmb: str, cmb: str, pack_qr: str, key: str) -> bool:
    zpl = build_bmb_cmb_combined_label(
        bmb_serial=short_bmb_cmb_id(bmb),
        cmb_serial=short_bmb_cmb_id(cmb),
        pack_qr_data=pack_qr,
    )
    return _show_zpl(zpl, f"bmb_cmb_{short_bmb_cmb_id(bmb)}.zpl", PRINTER_BMS_ID, key)


def _bms_complete() -> None:
    pack_qr = st.session_state.bms_pack_qr
    bmb, cmb = st.session_state.bms_bmb, st.session_state.bms_cmb
    if not _print_bms_pair(bmb, cmb, pack_qr, "bms"):
        return
    record = BmsScanRecord(
        date=st.session_state.bms_pack_date,
        time=datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
        pack_qr_data=pack_qr,
        bmb_id=bmb,
        cmb_id=cmb,
    )
    try:
        append_bms_record(record, excel_password=_password())
    except Exception as exc:
        st.error(f"Label generated, but scan log failed: {exc}")
        return
    _reset_bms()
    st.success("BMB/CMB label generated and logged.")


def _bms_rework(kind: str, new_id: str) -> None:
    pack_qr = st.session_state.bms_pack_qr
    existing = st.session_state.bms_existing
    if existing is None:
        st.warning("Load an existing pack record first.")
        return
    if not patch_rework(pack_qr, kind, new_id, excel_password=_password()):
        st.error("Could not update the rework record.")
        return
    bmb = new_id if kind == "BMB" else (
        st.session_state.bms_rework_bmb or existing.bmb_id
    )
    cmb = new_id if kind == "CMB" else (
        st.session_state.bms_rework_cmb or existing.cmb_id
    )
    if not _print_bms_pair(bmb, cmb, pack_qr, "bms_rework"):
        st.warning("Rework ID saved, but label printing failed.")
        return
    _reset_bms()
    st.success("Rework label generated.")


def _manual_dummy_page() -> None:
    st.header("Dummy ID (manual)")
    with st.form("manual_dummy_scan"):
        st.text_input("Scanner", key="manual_scan_input", placeholder="Scan pack serial or QR")
        st.form_submit_button("Process scan", on_click=_capture, args=("manual_scan_input",))
    raw = _take("manual_scan_input")
    if raw:
        result = identify_scan(raw)
        if result.scan_type != ScanType.SERIAL_NUMBER or result.serial is None:
            st.warning("Scan a valid pack serial or pack QR.")
            return
        st.session_state.manual_serial = result.serial.raw
        if result.serial.variant:
            st.session_state.manual_variant = result.serial.variant
        st.session_state.manual_category = (
            result.serial.category.title() if result.serial.category else "Domestic"
        )
        st.rerun()
    serial = st.session_state.manual_serial
    parsed = parse_serial_number(serial) if serial else None
    if not parsed:
        return
    st.caption(f"Serial: `{serial}` · Date: {parsed.format_date()}")
    if parsed.variant:
        variant = parsed.variant
        st.caption(f"Variant: {variant}")
    else:
        variant = st.selectbox("Variant", VARIANT_OPTIONS, key="manual_variant")
    ref_no = st.text_input("Ref No", key="manual_ref")
    if st.button("Generate dummy label", key="manual_print"):
        params = VARIANT_PARAMS[variant]
        category = st.session_state.get("manual_category", "Domestic")
        pack_qr = format_pack_qr_data(variant=variant, serial_number=serial)
        zpl = build_pack_qr_label(
            pack_qr_data=pack_qr,
            model_number=get_type_of_reess(variant),
            serial_number=serial,
            tac_number=params["tac_number"],
            mfg_date=parsed.format_date(),
            variant=variant,
            category=category,
        )
        if not _show_zpl(zpl, f"dummy_{serial}.zpl", PRINTER_DUMMY, "manual_dummy"):
            return
        record = ScanRecord(
            date=parsed.format_date(),
            time=datetime.now().strftime("%I:%M:%S %p").lstrip("0"),
            serial_count=parsed.serial_count,
            ref_no=ref_no.strip(),
            variant=variant,
            m1="Module Level Completed",
            m2="Module Level Completed",
            m3="Module Level Completed",
            m4="Module Level Completed",
            dummy_pack_status="INTEGRATION OK",
            dummy_pack_qr=pack_qr,
            serial_number=serial,
        )
        try:
            append_scan_record(record, excel_password=_password())
        except Exception as exc:
            st.error(f"Label generated, but scan log failed: {exc}")
            return
        st.session_state.manual_serial = ""
        st.success("Dummy label generated and logged.")


def _history_page() -> None:
    st.header("Scan History and Reprint")
    loaders = {
        "Battery Pack Dummy ID": (load_dummy_records, open_dummy_log, "dummy"),
        "Battery Pack ID": (load_pack_id_records, open_pack_id_log, "pack_id"),
        "BMB / CMB ID": (load_bms_records, open_bms_log, "bms"),
    }
    origin = st.selectbox("History", list(loaders), key="history_origin")
    loader, open_log, kind = loaders[origin]
    records = loader()
    if not records:
        st.info("No records found.")
        return
    st.dataframe(
        [{"Sl.No": i, **asdict(record)} for i, record in enumerate(records, 1)],
        width="stretch",
        hide_index=True,
    )
    index = st.selectbox(
        "Record",
        range(len(records)),
        format_func=lambda i: f"{i + 1}: {getattr(records[i], 'serial_number', '') or getattr(records[i], 'pack_qr_data', '')}",
    )
    if st.button("Reprint selected"):
        _history_reprint(records[index], kind, index + 1)
    left, right = st.columns(2)
    if left.button("Refresh history"):
        st.rerun()
    if right.button("Open Excel log"):
        _open_log(open_log)


def _history_reprint(record: Any, kind: str, index: int) -> None:
    try:
        if kind == "dummy":
            variant = record.variant if record.variant in VARIANT_OPTIONS else VARIANT_OPTIONS[-1]
            params = VARIANT_PARAMS[variant]
            if not record.dummy_pack_qr:
                raise ValueError("No pack QR data in this record.")
            zpl = build_pack_qr_label(
                pack_qr_data=record.dummy_pack_qr,
                model_number=get_type_of_reess(variant),
                serial_number=record.serial_number,
                tac_number=params["tac_number"],
                mfg_date=record.date,
                variant=variant,
                category="Domestic",
            )
            printer = PRINTER_DUMMY
            filename = f"reprint_dummy_{index}.zpl"
        elif kind == "pack_id":
            parsed = identify_scan(record.pack_qr_data)
            if parsed.serial is None or parsed.pack_variant is None:
                raise ValueError("Invalid pack QR data.")
            variant = record.variant if record.variant in VARIANT_OPTIONS else parsed.pack_variant
            params = VARIANT_PARAMS[variant]
            zpl = build_battery_pack_id_label(
                serial_number=record.serial_number or parsed.serial.raw,
                rated_capacity=params["rated_capacity"],
                max_voltage=params["max_voltage"],
                model_number=get_type_of_reess(variant),
                tac_number=params["tac_number"],
                variant=variant,
                category="Domestic",
                mfg_date=record.date,
                pack_qr_data=record.pack_qr_data,
            )
            printer = PRINTER_PACK_ID
            filename = f"reprint_pack_id_{index}.zpl"
        else:
            bmb = record.rework_bmb_id.strip() or record.bmb_id.strip()
            cmb = record.rework_cmb_id.strip() or record.cmb_id.strip()
            if not bmb or not cmb:
                raise ValueError("This record has no BMB/CMB pair.")
            zpl = build_bmb_cmb_combined_label(
                bmb_serial=short_bmb_cmb_id(bmb),
                cmb_serial=short_bmb_cmb_id(cmb),
                pack_qr_data=record.pack_qr_data,
            )
            printer = PRINTER_BMS_ID
            filename = f"reprint_bmb_cmb_{index}.zpl"
        _show_zpl(zpl, filename, printer, f"history_{kind}_{index}")
    except ValueError as exc:
        st.warning(str(exc))


def _open_log(open_fn: Any) -> None:
    if not hasattr(os, "startfile"):
        st.error("Opening Excel logs is supported on Windows only.")
        return
    try:
        open_fn()
    except OSError as exc:
        st.error(f"Could not open Excel log: {exc}")


def _admin_page() -> None:
    if st.session_state.role != ROLE_ADMIN:
        st.error("Admin Settings are available to Admin accounts only.")
        return
    st.header("Admin Settings")
    users, defects, messages, bms, pack = st.tabs(
        ["Manage Users", "Defect Log", "Messages", "BMS Scan Log", "Pack ID Scan Log"]
    )
    with users:
        _users_admin()
    with defects:
        _defect_admin(False)
    with messages:
        _defect_admin(True)
    with bms:
        st.dataframe(
            [asdict(record) for _, record in load_rework_records()],
            width="stretch",
            hide_index=True,
        )
        if st.button("Open BMS log"):
            _open_log(open_bms_log)
    with pack:
        st.dataframe(
            [asdict(record) for record in load_pack_id_records()],
            width="stretch",
            hide_index=True,
        )
        if st.button("Open Pack ID log"):
            _open_log(open_pack_id_log)


def _users_admin() -> None:
    st.dataframe(
        [{"Username": name, "Role": role.title()} for name, role in list_users()],
        width="stretch",
        hide_index=True,
    )
    with st.form("admin_add_user"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        role_label = st.selectbox("Role", ("Operator", "Admin"))
        submitted = st.form_submit_button("Add user")
    if submitted:
        role = ROLE_ADMIN if role_label == "Admin" else ROLE_OPERATOR
        ok, message = add_user(username, password, role)
        (st.success if ok else st.error)(message)
        if ok:
            st.rerun()
    others = [name for name, _ in list_users() if name != st.session_state.username]
    if others:
        name = st.selectbox("Remove user", others, key="admin_remove_user")
        if st.button("Remove selected user"):
            ok, message = delete_user(name, current_user=st.session_state.username)
            (st.success if ok else st.error)(message)
            if ok:
                st.rerun()


def _defect_admin(only_unacknowledged: bool) -> None:
    entries = (
        load_unacknowledged_issues()
        if only_unacknowledged
        else [(row, rec) for row, rec in load_all_records_with_rows() if rec.has_issue]
    )
    if not entries:
        st.info("No matching issue records.")
        return
    st.dataframe(
        [
            {
                "Excel row": row,
                "Date": rec.date,
                "Pack QR": rec.dummy_pack_qr,
                "Serial": rec.serial_number,
                "Issue 1": rec.issue_desc_1,
                "Issue 2": rec.issue_desc_2,
                "Action": rec.action_plan,
                "Remark": rec.remark,
                "Admin Acknowledgement": rec.admin_ack,
            }
            for row, rec in entries
        ],
        width="stretch",
        hide_index=True,
    )
    row = st.selectbox("Excel row", [item[0] for item in entries])
    record = next(rec for candidate, rec in entries if candidate == row)
    with st.form(f"ack_{only_unacknowledged}_{row}"):
        ack = st.text_input("Admin Acknowledgement", value=record.admin_ack)
        save = st.form_submit_button("Save acknowledgement")
    if save:
        if patch_admin_ack(row, ack.strip(), excel_password=_password()):
            st.success("Acknowledgement saved.")
            st.rerun()
        st.error("Could not save acknowledgement. Refresh and retry.")


def _sidebar() -> str:
    logo = CORE / "models" / "hv" / "assets" / "logo.png"
    with st.sidebar:
        if logo.is_file():
            st.image(str(logo), width=180)
        st.title("HV Label Printer")
        if st.session_state.username:
            st.caption(f"{st.session_state.role.title()}: {st.session_state.username}")
            if st.button("Log out", width="stretch"):
                st.session_state.username = None
                st.session_state.role = None
                st.session_state.excel_password = None
                st.rerun()
        else:
            with st.form("login"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                login = st.form_submit_button("Log in", width="stretch")
            if login:
                ok, user_or_message, role = authenticate_user(username, password)
                if ok:
                    st.session_state.username = user_or_message
                    st.session_state.role = role
                    st.session_state.excel_password = password
                    st.session_state.auth_notice_shown = False
                    st.rerun()
                st.error(user_or_message)
        st.divider()
        pages = [
            "Battery Pack Dummy ID",
            "Battery Pack ID",
            "BMS ID",
            "Dummy ID (manual)",
            "Scan History",
        ]
        if st.session_state.role == ROLE_ADMIN:
            pages.append("Admin Settings")
        page = st.selectbox("Page", pages, key="page")
        st.selectbox("Output", ("Print", "Save ZPL"), key="print_mode")
    if st.session_state.role == ROLE_ADMIN and not st.session_state.auth_notice_shown:
        count = len(load_unacknowledged_issues())
        if count:
            st.info(f"{count} unacknowledged issue(s) are available in Admin Settings.")
        st.session_state.auth_notice_shown = True
    return page


def main() -> None:
    _init()
    if not is_authorized():
        st.error("This machine is not authorized to run the HV Label Printer.")
        st.stop()
    page = _sidebar()
    {
        "Battery Pack Dummy ID": _dummy_page,
        "Battery Pack ID": _pack_id_page,
        "BMS ID": _bms_page,
        "Dummy ID (manual)": _manual_dummy_page,
        "Scan History": _history_page,
        "Admin Settings": _admin_page,
    }[page]()


main()
