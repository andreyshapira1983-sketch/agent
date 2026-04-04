import os
import json
import sqlite3
import psutil
import datetime
from pathlib import Path
from collections import defaultdict

class SafeExecutor:
    def __init__(self, db_path="test_executor.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.setup_database()
        self.execution_history = defaultdict(list)
        
    def setup_database(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT,
                output TEXT,
                error TEXT
            )
        ''')
        self.conn.commit()
        
    def execute_command(self, command):
        if not self.is_safe_command(command):
            return {"status": "blocked", "error": "Unsafe command"}
            
        timestamp = datetime.datetime.now()
        try:
            # Simulate command execution
            output = f"Executed: {command}"
            self.cursor.execute('''
                INSERT INTO executions (command, timestamp, status, output)
                VALUES (?, ?, ?, ?)
            ''', (command, timestamp, "success", output))
            self.conn.commit()
            
            self.execution_history[command].append({
                "timestamp": timestamp,
                "status": "success",
                "output": output
            })
            
            return {"status": "success", "output": output}
        except Exception as e:
            error_msg = str(e)
            self.cursor.execute('''
                INSERT INTO executions (command, timestamp, status, error)
                VALUES (?, ?, ?, ?)
            ''', (command, timestamp, "error", error_msg))
            self.conn.commit()
            
            return {"status": "error", "error": error_msg}
            
    def is_safe_command(self, command):
        unsafe_patterns = ["rm -rf", "format", "del /f", "shutdown", "reboot"]
        command_lower = command.lower()
        for pattern in unsafe_patterns:
            if pattern in command_lower:
                return False
        return True
        
    def get_execution_stats(self):
        self.cursor.execute('''
            SELECT status, COUNT(*) as count
            FROM executions
            GROUP BY status
        ''')
        stats = dict(self.cursor.fetchall())
        
        self.cursor.execute('''
            SELECT COUNT(*) as total FROM executions
        ''')
        total = self.cursor.fetchone()[0]
        
        return {
            "total_executions": total,
            "success_count": stats.get("success", 0),
            "error_count": stats.get("error", 0),
            "blocked_count": stats.get("blocked", 0)
        }
        
    def check_system_resources(self):
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu_usage": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_gb": memory.available / (1024**3),
            "disk_percent": disk.percent,
            "disk_free_gb": disk.free / (1024**3)
        }
        
    def export_history_json(self, output_file="execution_history.json"):
        self.cursor.execute('''
            SELECT * FROM executions
            ORDER BY timestamp DESC
        ''')
        
        columns = [desc[0] for desc in self.cursor.description]
        rows = self.cursor.fetchall()
        
        history = []
        for row in rows:
            history.append(dict(zip(columns, row)))
            
        with open(output_file, 'w') as f:
            json.dump(history, f, indent=2, default=str)
            
        return len(history)
        
    def cleanup_old_records(self, days=30):
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days)
        
        self.cursor.execute('''
            DELETE FROM executions
            WHERE timestamp < ?
        ''', (cutoff_date,))
        
        deleted = self.cursor.rowcount
        self.conn.commit()
        
        return deleted
        
    def close(self):
        self.conn.close()


def main():
    executor = SafeExecutor()
    
    # Test various commands
    test_commands = [
        "echo Hello World",
        "ls -la",
        "pwd",
        "rm -rf /",  # This should be blocked
        "date",
        "whoami"
    ]
    
    print("Testing SafeExecutor...")
    print("-" * 50)
    
    for cmd in test_commands:
        result = executor.execute_command(cmd)
        print(f"Command: {cmd}")
        print(f"Result: {result}")
        print("-" * 30)
        
    # Check system resources
    resources = executor.check_system_resources()
    print("\nSystem Resources:")
    for key, value in resources.items():
        print(f"{key}: {value}")
        
    # Get execution statistics
    stats = executor.get_execution_stats()
    print("\nExecution Statistics:")
    for key, value in stats.items():
        print(f"{key}: {value}")
        
    # Export history
    exported = executor.export_history_json()
    print(f"\nExported {exported} records to execution_history.json")
    
    # Cleanup
    executor.close()
    
    # Clean up test files
    if os.path.exists("test_executor.db"):
        os.remove("test_executor.db")
    if os.path.exists("execution_history.json"):
        os.remove("execution_history.json")


if __name__ == "__main__":
    main()