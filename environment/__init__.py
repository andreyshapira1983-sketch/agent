# environment — Environment Modeling (Слой 27) + Simulation/Sandbox (Слой 28)
#             + Operating System Layer (Слой 6)
# Модель мира, прогнозирование последствий, тестирование действий в песочнице.
# Единый фасад к ОС: файлы, процессы, пакеты, сеть, устройства, сервисы.
from .environment_model import EnvironmentModel, EnvironmentState
from .sandbox import SandboxLayer, SimulationRun, SandboxResult, ContainerSandbox
from .os_layer import OperatingSystemLayer, OSInfo, OSPlatform

__all__ = [
    'EnvironmentModel', 'EnvironmentState',
    'SandboxLayer', 'SimulationRun', 'SandboxResult', 'ContainerSandbox',
    'OperatingSystemLayer', 'OSInfo', 'OSPlatform',
]
