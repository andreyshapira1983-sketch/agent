# Tools: registry, orchestrator, impl
from src.tools.impl.time_tool import register_time_tool
from src.tools.impl.file_tools import register_file_tools
from src.tools.impl.run_pytest_tool import register_run_pytest_tool
from src.tools.impl.evolution_tools import register_evolution_tools
from src.tools.impl.agent_tools import register_agent_tools
from src.tools.impl.self_model_tools import register_self_model_tools
from src.tools.impl.tts_tool import register_tts_tool
from src.tools.impl.autonomy_tools import register_autonomy_tools
from src.tools.impl.patch_request_tool import register_patch_request_tool
from src.tools.impl.pip_tool import register_pip_tool
from src.tools.impl.run_shell_tools import register_run_shell_tools
from src.tools.impl.browser_reminder_tools import register_browser_reminder_tools
from src.tools.impl.code_index_tools import register_code_index_tools

register_time_tool()
register_file_tools()
register_run_pytest_tool()
register_evolution_tools()
register_agent_tools()
register_self_model_tools()
register_tts_tool()
register_autonomy_tools()
register_patch_request_tool()
register_pip_tool()
register_run_shell_tools()
register_browser_reminder_tools()
register_code_index_tools()
