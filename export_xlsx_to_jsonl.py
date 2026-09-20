import os
import json
from datetime import datetime, date
import openpyxl

def export_xlsx_to_jsonl(xlsx_path="douban_interests.xlsx", output_jsonl="douban_archive.jsonl"):
    """
    Exports all rows from all sheets of douban_interests.xlsx to a JSONL file.
    Ensures complete data retention without omission.
    """
    if not os.path.exists(xlsx_path):
        raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    total_exported = 0

    with open(output_jsonl, "w", encoding="utf-8") as out_f:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            headers = rows[0]
            # Data rows start from row index 1
            data_rows = rows[1:]
            sheet_count = 0

            for r in data_rows:
                # Skip completely empty rows if any
                if not any(r):
                    continue

                def format_val(val):
                    if val is None:
                        return None
                    if isinstance(val, (datetime, date)):
                        return val.strftime("%Y-%m-%d %H:%M:%S")
                    return val

                title = format_val(r[0]) if len(r) > 0 else None
                intro = format_val(r[1]) if len(r) > 1 else None
                douban_rating = format_val(r[2]) if len(r) > 2 else None
                link = format_val(r[3]) if len(r) > 3 else None
                create_time = format_val(r[4]) if len(r) > 4 else None
                my_rating = r[5] if len(r) > 5 else None
                tags = format_val(r[6]) if len(r) > 6 else None
                comment = format_val(r[7]) if len(r) > 7 else None
                visibility = format_val(r[8]) if len(r) > 8 else None

                record = {
                    "type": sheet_name,
                    "title": title,
                    "intro": intro,
                    "douban_rating": douban_rating,
                    "link": link,
                    "create_time": create_time,
                    "my_rating": my_rating,
                    "tags": tags,
                    "comment": comment,
                    "visibility": visibility,
                }

                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                sheet_count += 1
                total_exported += 1

            print(f"Exported sheet '{sheet_name}': {sheet_count} records")

    print(f"Total exported records: {total_exported} -> {output_jsonl}")
    return total_exported

if __name__ == "__main__":
    export_xlsx_to_jsonl()
