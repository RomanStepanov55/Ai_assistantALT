import os
import time
import threading
from brain.opus_local_core import OpusLocalCore, OpusConfig, emit_pet_command
from vision.omni_eye import OmniEye
from games.universal_explorer import UniversalExplorer
from dashboard.app import run_dashboard_server
from alontito.voice_assistant import OfflineVoiceAssistant
from alontito.omni_mcp_bridge import OmniMCPBridge


def main() -> None:
    cfg = OpusConfig.from_json("config_omni.json")
    brain = OpusLocalCore(config=cfg)
    eye = OmniEye(memory=brain.memory)
    explorer = UniversalExplorer(brain=brain, eye=eye)
    voice = OfflineVoiceAssistant(brain=brain)
    mcp_bridge = OmniMCPBridge(brain=brain, explorer=explorer)

    emit_pet_command("portal_pull_out", prop="laptop", emotion="excited")

    dash_thread = threading.Thread(
        target=run_dashboard_server,
        args=(brain, explorer, 8080),
        daemon=True
    )
    dash_thread.start()
    voice.start()

    print("[Alontito Omni] Дашборд запущен: http://127.0.0.1:8080")
    print("[Alontito Omni] Введите запрос (или /explore, /stop, /mcp_status, /quit):")

    try:
        while True:
            cmd = input("Alontito> ").strip()
            if not cmd:
                continue
            if cmd == "/quit":
                break
            elif cmd == "/explore":
                explorer.start_background()
                print("[UCB1] Автономное исследование запущено.")
            elif cmd == "/stop":
                explorer.stop()
                print("[UCB1] Исследование остановлено.")
            elif cmd == "/mcp_status":
                print(mcp_bridge.handle_tool_call("alontito_status", {}))
            else:
                res = brain.think_and_act(cmd)
                if res["thinking"]:
                    print(f"\n[Мысли]:\n{res['thinking']}\n")
                print(f"[Алонтито]: {res['answer']}\n")
    except KeyboardInterrupt:
        pass
    finally:
        explorer.stop()
        voice.stop()
        emit_pet_command("item_stow", emotion="sleepy")


if __name__ == "__main__":
    main()
