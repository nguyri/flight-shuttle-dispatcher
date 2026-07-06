import io
import logging
import os
from pathlib import Path
from PIL import Image
import pandas as pd
from google import genai
import csv as builtin_csv

logger = logging.getLogger(__name__)

def generate_prompt(target_headers: list = None) -> str:
    """Constructs and returns the engineered prompt instruction for the vision model."""
    prompt = (
        "You are an expert data extraction engine. Your job is to convert the image manifest into a raw CSV.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. EXTRACT EVERY ROW: Do not summarize, skip, or truncate any passenger lines. Every physical row in the image must have a corresponding row in the CSV.\n"
        "2. ROW ISOLATION: Each independent passenger line must be its own row. Do not merge multiple rows together.\n"
        "3. QUOTATION MARKS: Always wrap text values that contain spaces, commas, or special characters inside standard double quotes (e.g., \"Doe, John\").\n"
        "4. FORMAT: Return the output strictly as a valid CSV string. Do not include markdown code blocks, formatting markers (like ```csv), or chat commentary."
    )
    
    if target_headers:
        headers_str = ", ".join(target_headers)
        prompt += f"\nPlease format the columns to match these headers: [{headers_str}]"

    return prompt


def extract_table_from_image(image_file, target_headers: list = None) -> pd.DataFrame:
    """Sends an uploaded image to Gemini to extract table data into a Pandas DataFrame,
    gracefully filling broken lines with error indicators rather than dropping them.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        
        pil_img = Image.open(image_file)
        prompt = generate_prompt(target_headers)
            
        logger.info(f"Sending manifest image payload ({getattr(image_file, 'name', 'unnamed')}) to Gemini API...")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[pil_img, prompt]
        )
        
        raw_csv = response.text.replace("```csv", "").replace("```", "").strip()
        
        csv_reader = builtin_csv.reader(io.StringIO(raw_csv))
        
        # Read headers first
        try:
            headers = next(csv_reader)
        except StopIteration:
            return pd.DataFrame()
        
        # If we passed explicit target headers, use those as our master length guide
        expected_length = len(target_headers) if target_headers else len(headers)
        if target_headers:
            headers = target_headers # Enforce target structural rules
            
        processed_rows = []
        
        for line_no, row in enumerate(csv_reader, start=2): # Line 1 was header
            if not row: 
                continue # Skip pure blank trailing lines
                
            if len(row) != expected_length:
                logger.warning(f"Line {line_no} layout mismatch: Expected {expected_length} fields, saw {len(row)}.")
                
                if len(row) > expected_length:
                    # Case 1: Too many fields (e.g. 15 instead of 14). Truncate excess columns,
                    # but stamp the last field with an explicit warning indicator.
                    row = row[:expected_length]
                    row[-1] = f"⚠️ COL OVERFLOW (Orig length: {len(row)}) | {row[-1]}"
                else:
                    # Case 2: Too few fields. Pad out the missing columns with an explicit warning.
                    while len(row) < expected_length:
                        row.append("⚠️ MISSING FIELD DATA")
            
            processed_rows.append(row)
            
        # Compile everything safely back into a perfectly uniform DataFrame grid
        df = pd.DataFrame(processed_rows, columns=headers)
        return df

    except Exception as e:
        logger.error(f"Failed to extract table via Gemini API: {e}")
        raise RuntimeError(f"AI Table Parsing Failed: {e}")

def batch_extract_and_save_csv(image_files: list, target_headers: list, output_csv_path: Path) -> pd.DataFrame:
    """
    Iterates through multiple images, extracts data tables using Gemini,
    merges them, and automatically saves the combined CSV to disk.
    
    :return: Combined pd.DataFrame if successful, raises Exception otherwise.
    """
    all_dfs = []
    
    for idx, img_file in enumerate(image_files):
        try:
            df_part = extract_table_from_image(img_file, target_headers=target_headers)
            if not df_part.empty:
                all_dfs.append(df_part)
        except Exception as e:
            # Raise exception immediately or log it depending on severity
            raise RuntimeError(f"Error processing image '{img_file.name}' (Page {idx+1}): {e}")
            
    if not all_dfs:
        raise ValueError("No valid tabular data could be extracted from the provided images.")
        
    # Merge everything together
    combined_df = pd.concat(all_dfs, ignore_index=True)
    
    # Save directly to your temporary infrastructure path
    output_csv_path.parent.mkdir(parents=True, exist_ok=True)
    combined_df.to_csv(output_csv_path, index=False)
    logger.info(f"Successfully compiled and wrote combined manifest matrix to: {output_csv_path}")
    
    return combined_df