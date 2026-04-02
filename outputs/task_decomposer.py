import json
import re
from datetime import datetime
from collections import defaultdict
from pathlib import Path


class TaskDecomposer:
    def __init__(self, config_path='config.json'):
        self.config_path = Path(config_path)
        self.tasks = []
        self.subtasks = defaultdict(list)
        self.task_counter = 0
        self.load_config()
    
    def load_config(self):
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            self.config = {
                'max_subtask_depth': 3,
                'default_priority': 'medium',
                'task_categories': ['development', 'testing', 'documentation', 'deployment']
            }
    
    def create_task(self, title, description, category='development', priority='medium'):
        self.task_counter += 1
        task = {
            'id': self.task_counter,
            'title': title,
            'description': description,
            'category': category,
            'priority': priority,
            'created_at': datetime.now().isoformat(),
            'status': 'pending',
            'subtasks': []
        }
        self.tasks.append(task)
        return task['id']
    
    def decompose_task(self, task_id, subtask_descriptions):
        task = self._find_task(task_id)
        if not task:
            return False
        
        for desc in subtask_descriptions:
            subtask = {
                'id': f"{task_id}.{len(task['subtasks']) + 1}",
                'description': desc,
                'status': 'pending',
                'created_at': datetime.now().isoformat()
            }
            task['subtasks'].append(subtask)
            self.subtasks[task_id].append(subtask)
        
        return True
    
    def _find_task(self, task_id):
        for task in self.tasks:
            if task['id'] == task_id:
                return task
        return None
    
    def analyze_complexity(self, task_description):
        complexity_keywords = {
            'high': ['complex', 'advanced', 'difficult', 'intricate', 'sophisticated'],
            'medium': ['moderate', 'standard', 'regular', 'typical'],
            'low': ['simple', 'basic', 'easy', 'straightforward', 'trivial']
        }
        
        description_lower = task_description.lower()
        complexity_score = {'high': 0, 'medium': 0, 'low': 0}
        
        for level, keywords in complexity_keywords.items():
            for keyword in keywords:
                if keyword in description_lower:
                    complexity_score[level] += 1
        
        if complexity_score['high'] > 0:
            return 'high'
        elif complexity_score['medium'] > 0:
            return 'medium'
        else:
            return 'low'
    
    def suggest_decomposition(self, task_description):
        suggestions = []
        
        # Extract action words
        action_patterns = [
            r'\b(create|build|develop|implement|design|test|deploy|configure|setup|install)\b',
            r'\b(analyze|review|optimize|refactor|update|migrate|integrate)\b'
        ]
        
        for pattern in action_patterns:
            matches = re.findall(pattern, task_description.lower())
            for match in matches:
                if match in ['create', 'build', 'develop']:
                    suggestions.extend([
                        f"Design architecture for {match} functionality",
                        f"Implement core {match} logic",
                        f"Add error handling for {match} process",
                        f"Write tests for {match} feature"
                    ])
                elif match in ['test', 'deploy']:
                    suggestions.extend([
                        f"Prepare {match} environment",
                        f"Execute {match} procedures",
                        f"Validate {match} results",
                        f"Document {match} process"
                    ])
        
        return suggestions[:5] if suggestions else self._default_suggestions()
    
    def _default_suggestions(self):
        return [
            "Analyze requirements and constraints",
            "Design solution architecture",
            "Implement core functionality",
            "Add error handling and validation",
            "Write tests and documentation"
        ]
    
    def estimate_effort(self, task):
        base_effort = {
            'high': 8,
            'medium': 4,
            'low': 2
        }
        
        complexity = self.analyze_complexity(task['description'])
        effort_hours = base_effort.get(complexity, 4)
        
        # Adjust based on subtasks
        if task['subtasks']:
            effort_hours += len(task['subtasks']) * 1.5
        
        # Adjust based on category
        category_multipliers = {
            'development': 1.0,
            'testing': 0.8,
            'documentation': 0.6,
            'deployment': 1.2
        }
        
        multiplier = category_multipliers.get(task['category'], 1.0)
        return int(effort_hours * multiplier)
    
    def get_task_tree(self, task_id):
        task = self._find_task(task_id)
        if not task:
            return None
        
        tree = {
            'task': {
                'id': task['id'],
                'title': task['title'],
                'status': task['status'],
                'effort_hours': self.estimate_effort(task)
            },
            'subtasks': []
        }
        
        for subtask in task['subtasks']:
            tree['subtasks'].append({
                'id': subtask['id'],
                'description': subtask['description'],
                'status': subtask['status']
            })
        
        return tree
    
    def update_task_status(self, task_id, status):
        valid_statuses = ['pending', 'in_progress', 'completed', 'blocked']
        if status not in valid_statuses:
            return False
        
        task = self._find_task(task_id)
        if task:
            task['status'] = status
            task['updated_at'] = datetime.now().isoformat()
            return True
        return False
    
    def get_statistics(self):
        stats = {
            'total_tasks': len(self.tasks),
            'by_status': defaultdict(int),
            'by_category': defaultdict(int),
            'by_priority': defaultdict(int),
            'total_subtasks': sum(len(task['subtasks']) for task in self.tasks)
        }
        
        for task in self.tasks:
            stats['by_status'][task['status']] += 1
            stats['by_category'][task['category']] += 1
            stats['by_priority'][task['priority']] += 1
        
        return dict(stats)
    
    def export_tasks(self, output_path='tasks_export.json'):
        export_data = {
            'export_date': datetime.now().isoformat(),
            'tasks': self.tasks,
            'statistics': self.get_statistics()
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        return True
    
    def import_tasks(self, import_path):
        if not Path(import_path).exists():
            return False
        
        with open(import_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if 'tasks' in data:
            self.tasks.extend(data['tasks'])
            self.task_counter = max([t['id'] for t in self.tasks] + [0])
            
            for task in data['tasks']:
                if task['subtasks']:
                    self.subtasks[task['id']] = task['subtasks']
            
            return True
        return False


def main():
    decomposer = TaskDecomposer()
    
    # Create sample task
    task_id = decomposer.create_task(
        title="Build REST API",
        description="Create a complex REST API with authentication and database integration",
        category="development",
        priority="high"
    )
    
    # Get decomposition suggestions
    suggestions = decomposer.suggest_decomposition("Create a complex REST API with authentication and database integration")
    
    # Decompose the task
    decomposer.decompose_task(task_id, suggestions[:3])
    
    # Update status
    decomposer.update_task_status(task_id, "in_progress")
    
    # Get task tree
    tree = decomposer.get_task_tree(task_id)
    print(json.dumps(tree, indent=2))
    
    # Get statistics
    stats = decomposer.get_statistics()
    print("\nStatistics:", stats)
    
    # Export tasks
    decomposer.export_tasks()


if __name__ == "__main__":
    main()