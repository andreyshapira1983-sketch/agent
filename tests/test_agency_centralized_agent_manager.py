import unittest
from src.agency.centralized_agent_manager import CentralizedAgentManager

class TestCentralizedAgentManager(unittest.TestCase):
    def setUp(self):
        self.manager = CentralizedAgentManager()

    def test_add_agent(self):
        self.manager.add_agent('Agent1')
        self.assertEqual(len(self.manager.agents), 1)

    def test_coordinate_tasks(self):
        self.manager.add_agent('Agent1')
        # Псевдотест для координации задач
        self.manager.coordinate_tasks('Sample Task')

if __name__ == '__main__':
    unittest.main()
