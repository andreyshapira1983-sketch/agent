import os
import json
import sqlite3
import datetime
import collections
import math
from pathlib import Path

class DataProcessor:
    def __init__(self, db_path="data.db"):
        self.db_path = db_path
        self.connection = None
        self.init_database()
    
    def init_database(self):
        self.connection = sqlite3.connect(self.db_path)
        cursor = self.connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                category TEXT NOT NULL,
                value REAL NOT NULL,
                metadata TEXT
            )
        """)
        self.connection.commit()
    
    def add_record(self, category, value, metadata=None):
        cursor = self.connection.cursor()
        timestamp = datetime.datetime.now().isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        cursor.execute(
            "INSERT INTO records (timestamp, category, value, metadata) VALUES (?, ?, ?, ?)",
            (timestamp, category, value, meta_json)
        )
        self.connection.commit()
        return cursor.lastrowid
    
    def get_statistics(self, category=None):
        cursor = self.connection.cursor()
        if category:
            cursor.execute("SELECT value FROM records WHERE category = ?", (category,))
        else:
            cursor.execute("SELECT value FROM records")
        
        values = [row[0] for row in cursor.fetchall()]
        
        if not values:
            return {"count": 0, "sum": 0, "mean": 0, "min": 0, "max": 0}
        
        return {
            "count": len(values),
            "sum": sum(values),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "std_dev": math.sqrt(sum((x - sum(values)/len(values))**2 for x in values) / len(values))
        }
    
    def get_categories_summary(self):
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT category, COUNT(*) as count, AVG(value) as avg_value
            FROM records
            GROUP BY category
            ORDER BY count DESC
        """)
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "category": row[0],
                "count": row[1],
                "average": round(row[2], 2)
            })
        
        return results
    
    def export_to_json(self, output_file="export.json"):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM records ORDER BY timestamp DESC")
        
        records = []
        for row in cursor.fetchall():
            record = {
                "id": row[0],
                "timestamp": row[1],
                "category": row[2],
                "value": row[3],
                "metadata": json.loads(row[4]) if row[4] else None
            }
            records.append(record)
        
        with open(output_file, 'w') as f:
            json.dump({"records": records, "exported_at": datetime.datetime.now().isoformat()}, f, indent=2)
        
        return len(records)
    
    def cleanup_old_records(self, days=30):
        cursor = self.connection.cursor()
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        cursor.execute("DELETE FROM records WHERE timestamp < ?", (cutoff_date,))
        deleted = cursor.rowcount
        self.connection.commit()
        return deleted
    
    def close(self):
        if self.connection:
            self.connection.close()

def main():
    processor = DataProcessor()
    
    # Add sample data
    categories = ["sales", "expenses", "revenue", "costs", "profit"]
    for i in range(50):
        category = categories[i % len(categories)]
        value = (i + 1) * 10.5 + (i % 3) * 2.5
        metadata = {"source": f"system_{i % 3}", "verified": i % 2 == 0}
        processor.add_record(category, value, metadata)
    
    # Display statistics
    print("Overall Statistics:")
    stats = processor.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value:.2f}")
    
    print("\nCategory Summary:")
    for summary in processor.get_categories_summary():
        print(f"  {summary['category']}: {summary['count']} records, avg: {summary['average']}")
    
    # Export data
    exported = processor.export_to_json()
    print(f"\nExported {exported} records to export.json")
    
    # Cleanup
    processor.close()
    
    # File operations
    data_dir = Path("data_output")
    data_dir.mkdir(exist_ok=True)
    
    for i in range(5):
        file_path = data_dir / f"report_{i}.txt"
        with open(file_path, 'w') as f:
            f.write(f"Report #{i}\n")
            f.write(f"Generated at: {datetime.datetime.now()}\n")
            f.write(f"Data points: {i * 10 + 5}\n")
    
    print(f"\nCreated {len(list(data_dir.glob('*.txt')))} report files")

if __name__ == "__main__":
    main()