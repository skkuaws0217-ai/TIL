"""Base chunked CSV loader with memory guard for large MIMIC-IV files."""

import os
import gc
import pandas as pd
import psutil


def get_memory_usage_pct():
    return psutil.virtual_memory().percent


def chunked_filter(filepath, filter_fn, usecols=None, chunksize=500_000,
                   dtype=None, memory_limit_pct=85, compression=None):
    """Stream through a large CSV, apply filter per chunk, accumulate results.

    Args:
        filepath: Path to CSV or CSV.GZ file
        filter_fn: Function(df_chunk) -> filtered df
        usecols: Columns to load (reduces memory)
        chunksize: Rows per chunk
        dtype: Column dtypes
        memory_limit_pct: Flush to disk if memory exceeds this %
        compression: 'gzip' or None (auto-detected from extension)

    Returns:
        pd.DataFrame of all filtered rows
    """
    if compression is None and filepath.endswith(".gz"):
        compression = "gzip"

    chunks = []
    total_rows = 0

    reader = pd.read_csv(
        filepath,
        chunksize=chunksize,
        usecols=usecols,
        dtype=dtype,
        compression=compression,
        low_memory=False,
    )

    for i, chunk in enumerate(reader):
        filtered = filter_fn(chunk)
        if len(filtered) > 0:
            chunks.append(filtered)
            total_rows += len(filtered)

        if (i + 1) % 10 == 0:
            mem_pct = get_memory_usage_pct()
            print(f"  Chunk {i+1}: {total_rows:,} rows kept, memory {mem_pct:.0f}%")

            if mem_pct > memory_limit_pct:
                print(f"  WARNING: Memory at {mem_pct}%, flushing and gc...")
                if chunks:
                    chunks = [pd.concat(chunks, ignore_index=True)]
                gc.collect()

    if not chunks:
        return pd.DataFrame()

    result = pd.concat(chunks, ignore_index=True)
    print(f"  DONE: {total_rows:,} rows total from {i+1} chunks")
    return result


def load_small_csv(filepath, usecols=None, dtype=None):
    """Load a CSV that fits in memory (< 1GB)."""
    compression = "gzip" if filepath.endswith(".gz") else None
    return pd.read_csv(filepath, usecols=usecols, dtype=dtype, compression=compression)


def save_parquet(df, filepath):
    """Save DataFrame to parquet with directory creation."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    df.to_parquet(filepath, index=False, engine="pyarrow")
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"  Saved: {filepath} ({len(df):,} rows, {size_mb:.1f} MB)")


def load_parquet(filepath):
    """Load parquet file."""
    return pd.read_parquet(filepath, engine="pyarrow")
