import os
import io
import pandas as pd
from typing import Dict, Any, List, Optional

class SpreadsheetManager:
    def __init__(self):
        self.current_filename: str = ""
        self.df: Optional[pd.DataFrame] = None

    def load_from_bytes(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Load any CSV or Excel file into a pandas DataFrame."""
        self.current_filename = filename
        ext = os.path.splitext(filename)[1].lower()

        if ext in [".xlsx", ".xls"]:
            self.df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        else:
            # Attempt UTF-8 with fallback to Latin-1
            try:
                self.df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, encoding="utf-8")
            except Exception:
                self.df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, encoding="latin-1")

        self.df = self.df.fillna("")
        return self.get_summary()

    def load_from_filepath(self, filepath: str) -> Dict[str, Any]:
        """Load a file directly from a local filepath (e.g. Master Catalog)."""
        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            return self.load_from_bytes(f.read(), filename)

    def get_summary(self) -> Dict[str, Any]:
        if self.df is None:
            return {
                "loaded": False,
                "filename": "",
                "total_rows": 0,
                "total_cols": 0,
                "columns": []
            }

        return {
            "loaded": True,
            "filename": self.current_filename,
            "total_rows": len(self.df),
            "total_cols": len(self.df.columns),
            "columns": list(self.df.columns)
        }

    def get_rows(self, page: int = 1, page_size: int = 50, search: str = "") -> Dict[str, Any]:
        """Get paginated rows with optional search across all columns."""
        if self.df is None:
            return {"rows": [], "total": 0, "page": 1, "total_pages": 1}

        filtered_df = self.df
        if search.strip():
            q = search.strip().lower()
            mask = filtered_df.apply(lambda row: row.astype(str).str.lower().str.contains(q).any(), axis=1)
            filtered_df = filtered_df[mask]

        total_rows = len(filtered_df)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = start + page_size
        
        # Slice and convert to list of dicts with original row indices
        sliced_df = filtered_df.iloc[start:end]
        rows = []
        for idx, row in sliced_df.iterrows():
            row_dict = row.to_dict()
            row_dict["__row_idx__"] = int(idx)
            rows.append(row_dict)

        return {
            "rows": rows,
            "total": total_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "columns": list(self.df.columns)
        }

    def update_cell(self, row_idx: int, col_name: str, new_value: str) -> bool:
        """Update a single cell in the DataFrame."""
        if self.df is None or row_idx not in self.df.index or col_name not in self.df.columns:
            return False
        self.df.at[row_idx, col_name] = str(new_value)
        return True

    def add_row(self, row_dict: Dict[str, str]) -> int:
        """Add a new row to the DataFrame."""
        if self.df is None:
            self.df = pd.DataFrame([row_dict])
            return 0
        new_row = {col: row_dict.get(col, "") for col in self.df.columns}
        self.df.loc[len(self.df)] = new_row
        return len(self.df) - 1

    def delete_row(self, row_idx: int) -> bool:
        """Delete a row from the DataFrame."""
        if self.df is None or row_idx not in self.df.index:
            return False
        self.df = self.df.drop(index=row_idx).reset_index(drop=True)
        return True

    def export_data(self, output_format: str = "csv") -> tuple[bytes, str, str]:
        """
        Export current DataFrame into CSV or formatted Excel.
        Returns: (file_bytes, download_filename, media_type)
        """
        if self.df is None:
            raise Exception("No document loaded to export.")

        base_name = os.path.splitext(self.current_filename)[0] or "catalog_export"

        if output_format.lower() == "excel":
            out_filename = f"{base_name}.xlsx"
            out_buffer = io.BytesIO()
            with pd.ExcelWriter(out_buffer, engine="openpyxl") as writer:
                self.df.to_excel(writer, index=False, sheet_name="Products")
                
                # Apply OpenPyXL styling
                workbook = writer.book
                worksheet = writer.sheets["Products"]
                
                # Style header
                from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
                header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
                header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                alignment = Alignment(horizontal="left", vertical="center")
                
                for cell in worksheet[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = alignment

                # Auto-adjust column widths
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

            out_bytes = out_buffer.getvalue()
            return out_bytes, out_filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            out_filename = f"{base_name}.csv"
            out_bytes = self.df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            return out_bytes, out_filename, "text/csv"
