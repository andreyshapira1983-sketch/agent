import os
import json
import csv
import sqlite3
import re
from datetime import datetime
from collections import defaultdict, Counter
from itertools import chain
from pathlib import Path
import math

class DataProcessor:
    def __init__(self, data_dir="data", db_path="processed_data.db"):
        self.data_dir = Path(data_dir)
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self._init_database()
        
    def _init_database(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_type TEXT,
                processed_at TIMESTAMP,
                record_count INTEGER,
                status TEXT
            )
        ''')
        
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS data_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT,
                data_type TEXT,
                key TEXT,
                value TEXT,
                numeric_value REAL,
                created_at TIMESTAMP
            )
        ''')
        self.conn.commit()
        
    def process_json_file(self, filepath):
        records = []
        with open(filepath, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    for idx, item in enumerate(data):
                        if isinstance(item, dict):
                            for key, value in item.items():
                                numeric_val = None
                                if isinstance(value, (int, float)):
                                    numeric_val = float(value)
                                records.append((
                                    filepath.name,
                                    'json',
                                    f"{idx}_{key}",
                                    str(value),
                                    numeric_val,
                                    datetime.now()
                                ))
                elif isinstance(data, dict):
                    for key, value in data.items():
                        numeric_val = None
                        if isinstance(value, (int, float)):
                            numeric_val = float(value)
                        records.append((
                            filepath.name,
                            'json',
                            key,
                            str(value),
                            numeric_val,
                            datetime.now()
                        ))
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON file {filepath}: {e}")
                return 0
                
        if records:
            self.cursor.executemany('''
                INSERT INTO data_records (source_file, data_type, key, value, numeric_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', records)
            self.conn.commit()
        return len(records)
        
    def process_csv_file(self, filepath):
        records = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                for column, value in row.items():
                    numeric_val = None
                    try:
                        numeric_val = float(value)
                    except (ValueError, TypeError):
                        pass
                    records.append((
                        filepath.name,
                        'csv',
                        f"row_{row_idx}_{column}",
                        str(value),
                        numeric_val,
                        datetime.now()
                    ))
                    
        if records:
            self.cursor.executemany('''
                INSERT INTO data_records (source_file, data_type, key, value, numeric_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', records)
            self.conn.commit()
        return len(records)
        
    def process_text_file(self, filepath):
        records = []
        word_counter = Counter()
        line_lengths = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if line:
                    line_lengths.append(len(line))
                    words = re.findall(r'\b\w+\b', line.lower())
                    word_counter.update(words)
                    
        if line_lengths:
            avg_line_length = sum(line_lengths) / len(line_lengths)
            records.append((
                filepath.name,
                'text',
                'avg_line_length',
                str(avg_line_length),
                avg_line_length,
                datetime.now()
            ))
            
        records.append((
            filepath.name,
            'text',
            'total_lines',
            str(len(line_lengths)),
            float(len(line_lengths)),
            datetime.now()
        ))
        
        for word, count in word_counter.most_common(10):
            records.append((
                filepath.name,
                'text',
                f'word_freq_{word}',
                str(count),
                float(count),
                datetime.now()
            ))
            
        if records:
            self.cursor.executemany('''
                INSERT INTO data_records (source_file, data_type, key, value, numeric_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', records)
            self.conn.commit()
        return len(records)
        
    def process_all_files(self):
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True)
            
        processed_count = 0
        for filepath in self.data_dir.iterdir():
            if filepath.is_file():
                record_count = 0
                status = 'success'
                
                try:
                    if filepath.suffix == '.json':
                        record_count = self.process_json_file(filepath)
                    elif filepath.suffix == '.csv':
                        record_count = self.process_csv_file(filepath)
                    elif filepath.suffix in ['.txt', '.log']:
                        record_count = self.process_text_file(filepath)
                    else:
                        status = 'skipped'
                except Exception as e:
                    print(f"Error processing {filepath}: {e}")
                    status = 'error'
                    
                self.cursor.execute('''
                    INSERT INTO processed_files (filename, file_type, processed_at, record_count, status)
                    VALUES (?, ?, ?, ?, ?)
                ''', (filepath.name, filepath.suffix, datetime.now(), record_count, status))
                self.conn.commit()
                
                if status == 'success':
                    processed_count += 1
                    
        return processed_count
        
    def get_statistics(self):
        stats = {}
        
        self.cursor.execute('SELECT COUNT(*) FROM processed_files WHERE status = "success"')
        stats['total_processed_files'] = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT COUNT(*) FROM data_records')
        stats['total_records'] = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT data_type, COUNT(*) FROM data_records GROUP BY data_type')
        stats['records_by_type'] = dict(self.cursor.fetchall())
        
        self.cursor.execute('SELECT AVG(numeric_value) FROM data_records WHERE numeric_value IS NOT NULL')
        result = self.cursor.fetchone()[0]
        stats['avg_numeric_value'] = result if result else 0
        
        self.cursor.execute('''
            SELECT source_file, COUNT(*) as cnt 
            FROM data_records 
            GROUP BY source_file 
            ORDER BY cnt DESC 
            LIMIT 5
        ''')
        stats['top_files'] = self.cursor.fetchall()
        
        return stats
        
    def export_summary(self, output_file='summary.json'):
        stats = self.get_statistics()
        
        self.cursor.execute('''
            SELECT filename, file_type, processed_at, record_count, status
            FROM processed_files
            ORDER BY processed_at DESC
        ''')
        processed_files = []
        for row in self.cursor.fetchall():
            processed_files.append({
                'filename': row[0],
                'file_type': row[1],
                'processed_at': row[2],
                'record_count': row[3],
                'status': row[4]
            })
            
        summary = {
            'generated_at': datetime.now().isoformat(),
            'statistics': stats,
            'processed_files': processed_files
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
            
        return output_file
        
    def cleanup_old_records(self, days=30):
        cutoff_date = datetime.now().timestamp() - (days * 24 * 60 * 60)
        
        self.cursor.execute('''
            DELETE FROM data_records 
            WHERE julianday(created_at) < julianday('now', ?)
        ''', (f'-{days} days',))
        
        deleted_count = self.cursor.rowcount
        self.conn.commit()
        
        return deleted_count
        
    def close(self):
        self.conn.close()

def main():
    processor = DataProcessor()
    
    try:
        print("Starting data processing...")
        processed = processor.process_all_files()
        print(f"Processed {processed} files successfully")
        
        stats = processor.get_statistics()
        print("\nStatistics:")
        print(f"Total records: {stats['total_records']}")
        print(f"Records by type: {stats['records_by_type']}")
        print(f"Average numeric value: {stats['avg_numeric_value']:.2f}")
        
        summary_file = processor.export_summary()
        print(f"\nSummary exported to: {summary_file}")
        
        old_records = processor.cleanup_old_records(90)
        print(f"Cleaned up {old_records} old records")
        
    finally:
        processor.close()

if __name__ == "__main__":
    main()