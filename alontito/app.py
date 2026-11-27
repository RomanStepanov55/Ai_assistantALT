"""Alontito - a terminal companion.

One application, one prompt: talk to the agent, remember facts, set
reminders, and read what was measured. Alontito does not play for the user
(HARD RULE #2, 2026-11-24) and since the 2026-11-27 pivot it has no game
layer at all: no Roblox launch, no HUD reader, no input code. Every number
shown here comes from a record or a measurement; when there is no number,
the app says so instead of printing 0.

Commands are listed in /help. Free text is routed by brain.interpret() when
it is obvious («память», «навыки»), and refused when it is not.
"""
import json
import os
import sys
from pathlib import Path

from . import omni_link
from .agent import Agent
from .brain import Brain, interpret
from .memory import JOURNAL_FILE, STATUSES, Memory
from .skills import guard_report
from .mcp_server import AlontitoMCPServer
from .pet_terminal import pet3d
from .ui import (box, clear_screen, clip, err, home_screen, info, ok,
                 paint, prompt, ready, say, set_background, step, table,
                 term_width, two_columns, warn, blank)

VERSION = "2.0.0"

HOME_COMMANDS = [
    ("/help", "справка"),
    ("/memory", "факты"),
    ("/skills", "навыки"),
    ("/journal", "журнал"),
    ("/learn", "что известно"),
    ("/autonomy", "саморазвитие"),
    ("/safety", "запреты"),
    ("/mcp", "MCP-мост для ИИ"),
    ("/3d", "3D-ассистент"),
    ("/omni", "обучение Omni"),
    ("/info", "об агенте"),
    ("/about", "о проекте"),
    ("/github", "репозиторий"),
    ("/support", "связь"),
    ("/quit", "выход"),
]


class App:
    def __init__(self):
        self.memory = Memory()
        self.brain = Brain(self.memory)
        self.agent = Agent(memory=self.memory)
        self.mcp = AlontitoMCPServer(memory=self.memory)
        self.running = True
        self._mcp_enabled = bool(self.memory.data.get("mcp_enabled", False))

    # ------------------------------------------------------------------
    # shell
    # ------------------------------------------------------------------

    def start(self):
        self.refresh_home()
        if pet3d.first_run_prompt():
            self.refresh_home()
        else:
            pet3d.autostart()
        while self.running:
            print(ready())
            raw = prompt("  › ")
            line = (raw or "").strip()
            if not line:
                continue
            if pet3d.handle_command(line):
                continue
            if line.startswith("/"):
                command = interpret(line)
                if command is None:
                    err(f"неизвестная команда: {line.split()[0]}. /help")
                    continue
                self.dispatch(command, line)
            else:
                self.cmd_say(line)

    def refresh_home(self):
        clear_screen()
        facts = len(self.memory.facts())
        questions = len(self.memory.open_questions())
        skills = len(self.memory.skills())
        pet_badge = pet3d.badge("ansi")
        pet_on = pet3d.ctl.status == "on"
        print(home_screen(VERSION, "—", facts, questions, skills,
                          HOME_COMMANDS, mcp_active=self._mcp_enabled,
                          pet_status=pet_badge, pet_on=pet_on))
        print()

    def dispatch(self, command: str, raw: str):
        name, _, rest = command.partition(" ")
        args = rest.split() if rest.strip() else []
        handler = {
            "/help": self.cmd_help,
            "/learn": self.cmd_learn,
            "/autonomy": self.cmd_autonomy,
            "/memory": self.cmd_memory,
            "/skills": self.cmd_skills,
            "/journal": self.cmd_journal,
            "/safety": self.cmd_safety,
            "/mcp": self.cmd_mcp,
            "/3d": self.cmd_3d,
            "/3д": self.cmd_3d,
            "/omni": self.cmd_omni,
            "/info": self.cmd_info,
            "/about": self.cmd_info,
            "/github": self.cmd_github,
            "/support": self.cmd_support,
            "/theme": self.cmd_theme,
            "/home": self.cmd_home,
            "/quit": self.cmd_quit,
            "/exit": self.cmd_quit,
        }.get(name)
        if handler is None:
            err(f"неизвестная команда: {name}. Используй /help")
            return
        try:
            handler(args)
        except KeyboardInterrupt:
            blank()
            warn("прервано пользователем — действие остановлено")
        except Exception as e:                      # never kill the shell
            err(f"ошибка выполнения: {e}")

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    def cmd_say(self, text: str):
        """Свободный текст — разговор с агентом (навыки или свой мозг)."""
        blank()
        reply = self.agent.answer(text, device="terminal")
        color = {"skill": "green", "own": "violet", "offline": "yellow"}.get(
            reply.get("source"), "muted")
        say(box("АЛОНТИТО", [reply.get("text") or ""], width=term_width(),
                color=color))
        if reply.get("source") == "offline":
            step("свой мозг не подключён: config/alontito_brain.json")
        blank()

    def cmd_help(self, args):
        blank()
        width = term_width()
        say(box("КОМАНДЫ", [
            two_columns([
                ("/memory [слово]", "факты и статусы"),
                ("/skills", "успех и прирост/мин"),
                ("/journal [n]", "события журнала"),
                ("/learn", "что известно из замеров"),
                ("/autonomy", "саморазвитие: метрики и эксперимент"),
                ("/safety", "границы действий"),
                ("/mcp", "MCP-мост для ИИ"),
                ("/3d", "3D-ассистент"),
                ("/omni", "обучение Omni"),
                ("/info", "об агенте"),
                ("/about", "о проекте"),
                ("/github", "репозиторий"),
                ("/support", "связь"),
                ("/home", "главный экран"),
                ("/theme", "фон вкл/выкл"),
                ("/quit", "выход"),
            ], width - 6),
            "",
            paint("Алонтито не играет за тебя (2026-11-24) и игры в нём больше "
                  "нет (2026-11-27): он помнит, подсказывает и напоминает.",
                  "muted", dim=True),
        ], width=width))
        blank()

    # ------------------------------------------------------------------ learn

    def cmd_learn(self, args):
        blank()
        summary = self.brain.summary()
        runs = self.brain.last_runs(5)
        avg = summary["avg_per_minute"]
        rows = [[r["at"][-8:], f"{r['gain']:,}" if r.get("gain") else "—",
                 str(r.get("per_minute") or "—"), str(r.get("seconds") or "—"),
                 (r.get("note") or "")[:22]] for r in runs]
        say(box("ЧТО ИЗВЕСТНО", [
            f"сессий: {summary['sessions']} · прогонов: {summary['runs']} "
            f"(подтверждённых {summary['verified_runs']})",
            f"суммарный подтверждённый прирост: {summary['total_gain']:,}",
            ("средний темп: " + f"{avg:,}/мин") if avg else
            "средний темп: нет данных",
            f"лучший навык: {summary['best_skill'] or 'нет замеров'}",
            "",
            table(["время", "прирост", "в мин", "сек", "заметка"], rows,
                  width=term_width() - 8)
            if rows else "прогонов пока нет — замеры появятся из наблюдений",
        ], width=term_width()))
        blank()

        proposal = self.brain.propose()
        if proposal is None:
            info("своих улучшений не предлагаю: Алонтито не играет, ему нечего "
                 "менять в игре. Саморазвитие агента смотрите в /autonomy.")
        else:
            say(box("ОДНО УЛУЧШЕНИЕ ЗА ИТЕРАЦИЮ", [
                f"проблема:  {proposal['problem']}",
                f"изменение: {proposal['change']}",
                f"метрика:   {proposal['metric']}",
                f"откат:     {proposal['rollback']}",
                f"основание: {proposal['evidence']}",
            ], width=term_width(), color="cyan"))
            step("правило: замер до → ровно это изменение → замер после. "
                 "Хуже — откат.")
        blank()

        cycle = self.brain.development()
        active = cycle.get("active")
        lines = [
            f"закрыто экспериментов: {cycle.get('closed', 0)} "
            f"(принято {cycle.get('accepted', 0)}, отклонено {cycle.get('rejected', 0)})",
        ]
        if active:
            lines += [
                f"сейчас: {active.get('change')}",
                f"попыток замера: {active.get('tries', 0)} · последний вердикт: {active.get('last_verdict') or 'ещё нет'}",
                "правило: один эксперимент, пока замер не скажет лучше / хуже / без изменений дважды",
            ]
        else:
            lines.append("открытого эксперимента нет — следующее /learn заведёт его, если есть замер")
        for lesson in cycle.get("lessons") or []:
            lines.append("урок: " + lesson)
        say(box("САМОРАЗВИТИЕ", lines, width=term_width(), color="cyan"))
        blank()

        questions = self.memory.open_questions()
        if questions:
            info(f"открытых вопросов: {len(questions)}")
            for q in questions[:5]:
                step(f"{q['question']} → {q['metric']}")
            blank()

    # ----------------------------------------------------------------- memory

    def cmd_autonomy(self, args):
        """Саморазвитие: метрики, активный эксперимент, уроки.
        /autonomy synth <имя> <задача> — агент пишет себе навык сам."""
        if args and args[0] == "synth":
            if len(args) < 3:
                err("нужно: /autonomy synth <имя> <задача одним текстом>")
                return
            name, task = args[1], " ".join(args[2:])
            step(f"синтез навыка «{name}»: мозг пишет код, страж AST проверяет, "
                 f"автотест запускает…")
            result = self.agent.synthesize_skill(name, task)
            if result["ok"]:
                ok(result["detail"])
            else:
                err(result["detail"])
            blank()
            return
        blank()
        st = self.agent._autonomy().status()
        metrics = st.get("metrics") or {}
        active = st.get("active")
        rows = []
        for key, label in (("memory_health", "память"),
                           ("helpfulness", "полезность ответов"),
                           ("autonomy", "успех навыков"),
                           ("punctuality", "пунктуальность напоминаний")):
            value = metrics.get(key)
            rows.append([label, "—" if value is None else f"{value * 100:.0f}%"])
        facts = metrics.get("facts")
        if facts is not None:
            rows.append(["фактов в памяти", str(facts)])
        contradictions = metrics.get("contradictions")
        if contradictions is not None:
            rows.append(["противоречий", str(contradictions)])
        body = [
            table(["метрика", "значение"], rows, width=term_width() - 8),
            "",
        ]
        if active:
            body += [
                f"эксперимент: {active.get('change')}",
                f"метрика: {active.get('metric')} · попыток: {active.get('tries')}",
                f"основание: {active.get('evidence')}",
            ]
        else:
            body.append("активного эксперимента нет — агент ждёт измерений")
        closed, accepted = st.get("closed") or 0, st.get("accepted") or 0
        body.append(f"закрыто экспериментов: {closed} (принято {accepted}, "
                    f"отклонено {st.get('rejected') or 0})")
        lessons = st.get("lessons") or []
        if lessons:
            body.append("")
            body.append("уроки:")
            body += [f"· {l}" for l in lessons[-5:]]
        params = self.agent._autonomy_params()
        body.append(f"параметры: review_rounds={params.get('review_rounds')}, "
                    f"interval_min={params.get('interval_min')}")
        say(box("САМОРАЗВИТИЕ (один эксперимент за раз, вердикт только по замеру)",
                body, width=term_width(), color="cyan"))
        blank()

    def cmd_memory(self, args):
        needle = " ".join(args)
        items = (self.memory.find_facts(needle) if needle
                 else self.memory.facts())
        blank()
        if not items:
            warn("фактов нет" if not needle
                 else f"по «{needle}» ничего не найдено")
            blank()
            return
        rows = [[f["date"], f["status"][:30], f["text"][:44], f["source"][:22]]
                for f in items[-25:]]
        say(box("ПАМЯТЬ", [
            table(["дата", "статус", "факт", "источник"], rows,
                  width=term_width() - 8),
        ], width=term_width()))
        counts = {s: len(self.memory.facts(s)) for s in STATUSES}
        numbered = " · ".join(f"{s[:22]} = {n}"
                              for s, n in counts.items() if n)
        if numbered:
            say("  " + paint(numbered, "muted"))
        blank()

    def cmd_skills(self, args):
        blank()
        skills = self.memory.skills()
        if not skills:
            warn("навыки ещё не запускались: статистики нет. Это не нули.")
            blank()
            return
        rows = [s.row() for s in skills]
        say(box("НАВЫКИ", [
            table(["навык", "попытки", "успех", "прирост/мин", "сек/попытка",
                   "последняя ошибка"], rows, width=term_width() - 8),
            "",
            paint("неподтверждённые попытки не попадают в успех: пусто ≠ ноль",
                  "muted", dim=True),
        ], width=term_width()))
        blank()

    def cmd_journal(self, args):
        n = 15
        if args:
            try:
                n = max(1, min(200, int(args[0])))
            except ValueError:
                err("количество должно быть числом")
                return
        path = Path(JOURNAL_FILE)
        blank()
        if not path.exists():
            warn("журнал пуст — событий ещё не было")
            blank()
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for raw in lines[-n:]:
            try:
                item = json.loads(raw)
            except Exception:
                say("  " + paint(raw[:100], "muted"))
                continue
            kind = item.get("kind", "?")
            at = item.get("at", "")[-8:]
            rest = {k: v for k, v in item.items()
                    if k not in ("kind", "at", "ts")}
            text = ", ".join(f"{k}={v}" for k, v in list(rest.items())[:4])
            say(f"  {paint(at, 'muted')}  {paint(kind.ljust(11), 'violet')} "
                f"{text[:84]}")
        blank()

    def cmd_safety(self, args):
        blank()
        say(box("ЗАПРЕЩЕНО СТРУКТУРНО", guard_report(), width=term_width(),
                color="yellow"))
        blank()
        say(box("КАК ЭТО ДЕРЖИТСЯ", [
            "1. Игрового слоя не существует: код ввода, зрение HUD и каталог "
            "игр удалены из проекта (2026-11-27), а не выключены флагом",
            "2. Запускать Roblox некому и нечем: ни одного вызова запуска "
            "в коде не осталось",
            "3. Действия на ПК требуют явного подтверждения с устройства и "
            "выключены по умолчанию",
            "4. Трейды и покупки не реализованы вовсе — не «выключены флагом»",
        ], width=term_width(), color="green"))
        blank()

    def cmd_mcp(self, args):
        """MCP server configuration and control command."""
        blank()
        sub = (args[0].lower() if args else "status")
        if sub in ("on", "enable", "start"):
            self._mcp_enabled = True
            self.memory.data["mcp_enabled"] = True
            self.memory.save()
            ok("MCP-мост активирован! Внешние ИИ (Claude, Cursor, Antigravity) могут подключаться.")
            info("Запуск в режиме stdio-сервера: python -m alontito.mcp_server")
        elif sub in ("off", "disable", "stop"):
            self._mcp_enabled = False
            self.memory.data["mcp_enabled"] = False
            self.memory.save()
            ok("MCP-мост отключен.")
        else:
            status_text = paint("АКТИВЕН", "green", bold=True) if self._mcp_enabled else paint("ВЫКЛЮЧЕН", "muted")
            tools = self.mcp.list_tools()
            tools_list = [f"• {t['name']}: {t['description'][:50]}…" for t in tools]
            say(box("MODEL CONTEXT PROTOCOL (MCP)", [
                f"Статус: {status_text}",
                f"Протокол: {self.mcp.get_server_info()['protocolVersion']} | Инструментов: {len(tools)}",
                "",
                paint("Команды управления:", "lilac", bold=True),
                "  /mcp on   — включить MCP-мост",
                "  /mcp off  — выключить MCP-мост",
                "",
                paint("Доступные MCP-инструменты для внешних ИИ:", "lilac", bold=True),
            ] + tools_list + [
                "",
                paint("Для подключения добавьте в config внешнего агента команду:", "muted"),
                paint(f"  python {Path(__file__).resolve().parent / 'mcp_server.py'}", "violet"),
            ], width=term_width(), color="purple"))
        blank()

    def cmd_3d(self, args):
        cmd_line = "/3d " + " ".join(args) if args else "/3d"
        pet3d.handle_command(cmd_line)

    def cmd_omni(self, args):
        """Read-only report on the Omni continual-learning layer.

        Reading files and a local SQLite snapshot is the whole action: this
        command never starts the launcher, the dashboard or any game input.
        """
        blank()
        width = term_width()
        st = omni_link.status()
        cfg = st["config"]

        if st["installed"]:
            core = paint(f"{st['core_present']}/{st['core_total']} файлов на месте",
                         "green")
        else:
            core = (paint(f"{st['core_present']}/{st['core_total']} файлов", "yellow")
                    + paint(" — нет: " + ", ".join(st["core_missing"]), "muted"))

        model_line = (paint(str(cfg.get("deep_model", "не задана")), "lilac")
                      + paint(" · fallback ", "muted")
                      + paint(str(cfg.get("fallback_deep_model", "не задана")), "lilac")
                      + paint(" · vision ", "muted")
                      + paint(str(cfg.get("vision_model", "не задана")), "lilac"))

        mem = st["memory"]
        if mem["experiences"] is None:
            memory_line = paint("базы ещё нет — исследователь не запускался", "muted")
        else:
            memory_line = paint(f"{mem['visual_states']} состояний · "
                                f"{mem['experiences']} опытов", "lilac")

        learn = st["learning"]
        if learn["examples"] is None:
            learning_line = paint("базы ещё нет — примеров 0", "muted")
        else:
            learning_line = paint(
                f"{learn['examples']} примеров · {learn['approved']} одобрено · "
                f"{learn['pending']} ждёт экспорта", "lilac")
        curated = ("датасет не создан" if st["curated"] is None
                   else f"{st['curated']} строк в датасете")

        probe = omni_link.ollama_status()
        if probe["state"] == "ok":
            ollama_line = paint(f"отвечает {probe['url']}", "green")
            ollama_line += paint(f" · моделей {len(probe['models'])}", "muted")
            absent = [f"{role}: {name}" for role, name in probe.get("wanted", {}).items()
                      if not probe.get("present", {}).get(role)]
            if absent:
                ollama_line += paint(" · не найдены " + ", ".join(absent), "yellow")
        else:
            ollama_line = (paint(f"не отвечает {probe['url']}", "yellow")
                           + paint(" — локальные модели не запущены", "muted"))

        if learn["eval_runs"] is None:
            eval_line = paint("нет данных", "muted")
        else:
            eval_line = paint(f"{learn['eval_runs']} прогонов · "
                              f"{learn['promotions']} повышений", "lilac")

        lessons = []
        for item in st["lessons"]:
            text = (item.get("lesson") or item.get("outcome") or "").strip()
            text = " ".join(text.split())
            lessons.append(paint("· ", "purple") + clip(text, width - 8))
        if not lessons:
            lessons = [paint("опыта пока нет", "muted")]

        pkgs = st["missing_packages"]
        if pkgs:
            pkg_line = paint(", ".join(name for name, _ in pkgs), "muted")
        else:
            pkg_line = paint("все опциональные пакеты установлены", "green")

        say(box("OMNI · НЕПРЕРЫВНОЕ ОБУЧЕНИЕ", [
            f"ядро      {core}",
            paint("конфиг    ", "muted") + model_line,
            f"Ollama    {ollama_line}",
            paint("память    ", "muted") + memory_line,
            paint("обучение  ", "muted") + learning_line + paint(f" · {curated}", "muted"),
            paint("оценка    ", "muted") + eval_line,
            "",
            paint("последний опыт", "lilac", bold=True),
        ] + lessons + [
            "",
            paint("Режим: только чтение. Терминал ничего не запускает.",
                  "muted"),
            paint("Исследователь UCB1 удалён вместе с игровым слоем: ранжировать "
                  "пока нечего. Библиотеки ждут потребителя в приложении.", "muted"),
            "",
            paint("Подключение:", "lilac", bold=True),
            paint("  ядро (opus_local_core) подключается к приложению как "
                  "провайдер «own»; конфиг — config/alontito_brain.json", "muted"),
            "",
            paint("не хватает: ", "muted") + pkg_line,
        ], width=width, color="purple"))
        blank()

    def cmd_info(self, args):
        blank()
        say(box("ОБ АГЕНТЕ", [
            f"Алонтито v{VERSION} · open-source · MIT",
            "Личный ИИ-компаньон: память с источниками, консультации и "
            "напоминания. Приложение открывается на любом устройстве в "
            "домашней сети; мозг — свой, локальный.",
            "",
            paint("Он не угадывает: нет числа — пишет «не подтверждено». Одно "
                  "улучшение за итерацию, замер до и после, откат если хуже.",
                  "muted"),
        ], width=term_width()))
        blank()

    def cmd_github(self, args):
        blank()
        root = Path(__file__).resolve().parent.parent
        is_repo = (root / ".git").exists()
        say(box("РЕПОЗИТОРИЙ", [
            f"путь: {root}",
            ("git-репозиторий инициализирован" if is_repo else
             paint("git не инициализирован — удалённого репозитория нет. "
                   "Ссылку не выдумываю: её нужно создать и указать.", "muted")),
            "",
            paint("история прежнего игрового кода остаётся в git — рабочее "
                  "дерево чистое", "muted"),
        ], width=term_width()))
        blank()

    def cmd_support(self, args):
        blank()
        say(box("СВЯЗЬ", [
            "канал поддержки не настроен: в проекте нет ни почты, ни сервера.",
            paint("Я не подставляю выдуманный адрес. Если нужен канал — его "
                  "надо завести и прописать здесь.", "muted"),
            "",
            "что можно сделать сейчас: /journal покажет события, /memory — "
            "факты, /safety — ограничения.",
        ], width=term_width()))
        blank()

    def cmd_theme(self, args):
        self._background = not self._background
        set_background(self._background)
        self.refresh_home()
        info("фон " + ("включён" if self._background else "выключен"))

    def cmd_home(self, args):
        self.refresh_home()
        blank()

    def cmd_quit(self, args):
        self.running = False
        self.memory.journal("quit", version=VERSION)
        self.memory.save()
        blank()
        info("состояние сохранено. До связи.")
        blank()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    app = App()
    try:
        app.memory.session_tick()
        app.start()
    except KeyboardInterrupt:
        blank()
        info("выход по Ctrl+C")
    except EOFError:
        blank()
        info("поток ввода закрыт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
