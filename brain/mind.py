"""
The Mind - the self-developing decision core.

Instead of a fixed instruction, the Mind owns a CAPABILITY REGISTRY (its list
of possibilities) and a PLAYBOOK (its own instruction) which it REWRITES BY
ITSELF after every action:

  1. DECIDE: score all available capabilities (UCB: exploit what worked +
     explore what is untried) and pick the best for the current context.
  2. EXECUTE: run the capability (guarded; cost measured).
  3. FEEDBACK: measure the reward (strength/gems deltas, success flags) and
     update the capability's self-stats.
  4. REFLECT: periodically regenerate the playbook (its own instruction),
     write insights, register newly DISCOVERED capabilities (unknown UIs,
     new machines/eggs/stands) - the list of possibilities grows by itself.
"""
import json
import time
from typing import Dict, Optional

from config import DATA_DIR
from brain.capabilities import CapabilityRegistry, Capability
from vision.ocr_engine import CYRILLIC_MAP

PLAYBOOK_FILE = DATA_DIR / "playbook.json"
PLAYBOOK_TXT = DATA_DIR / "playbook.txt"


class Mind:
    def __init__(self, actions, detector, watcher, memory, navigator,
                 pets, eggs, boss, rebirth, telemetry):
        self.a = actions
        self.det = detector
        self.watch = watcher
        self.memory = memory
        self.nav = navigator
        self.pets = pets
        self.eggs = eggs
        self.boss = boss
        self.rebirth = rebirth
        self.telemetry = telemetry

        self.registry = CapabilityRegistry()
        self._register_base_capabilities()
        self.last_feedback_ts = 0.0
        self.last_reflection = 0.0
        self.pending_reward_ctx = None
        self._last_hud = {}
        self._last_scene = {}
        self._last_ctx = {}

    # ================================================================
    # CAPABILITY DEFINITIONS
    # Each: name, description, when(hud, scene, ctx), execute(ctx)
    # ================================================================

    def _register_base_capabilities(self):
        R = self.registry

        def when_gift(hud, scene, ctx):
            return bool(hud.get("gift_ready")) or \
                time.time() - ctx.get("last_gift_attempt", 0) > 600.0

        def do_gift(ctx):
            ok = self.a.claim_gifts()
            ctx["last_gift_attempt"] = time.time()
            return ok

        R.register(Capability(
            "CLAIM_GIFTS", "Собрать накопленный timed gift (бесплатно)",
            when_gift, do_gift, reward_kind="gems", cooldown=180.0))

        def when_boss(hud, scene, ctx):
            return bool(set(scene.get("events", [])) & {"boss"})

        def do_boss(ctx):
            if self.boss.boss_travel_pending(ctx.get("scene")):
                res = self.boss.fight(ctx.get("scene"))
                return bool(res.get("fought"))
            self.a.look_around()
            return False

        R.register(Capability(
            "BOSS_FIGHT", "Найти диалог телепорта к боссу, уничтожить босса",
            when_boss, do_boss, reward_kind="items", cooldown=120.0,
            danger_ok=True))

        def when_quests(hud, scene, ctx):
            return time.time() - ctx.get("last_quest_check", 0) > ctx.get(
                "quest_interval", 300.0)

        def do_quests(ctx):
            claims = self.a.check_quests()
            n = len(claims) if isinstance(claims, list) else 0
            # adaptive interval: dry checks stretch it (self-tuning)
            if n:
                ctx["quest_interval"] = 300.0
            else:
                ctx["quest_interval"] = min(900.0, ctx.get("quest_interval", 300.0) + 300.0)
            return n > 0

        R.register(Capability(
            "CHECK_QUESTS", "Проверить квесты и забрать выполненные (бесплатно)",
            when_quests, do_quests, reward_kind="items", cooldown=60.0))

        def when_wheel(hud, scene, ctx):
            return time.time() - ctx.get("last_wheel_spin", 0) > 900.0

        def do_wheel(ctx):
            ok = self.a.spin_wheel()
            ctx["last_wheel_spin"] = time.time()
            return ok

        R.register(Capability(
            "SPIN_WHEEL", "Бесплатный спин колеса ENCHANT PETS (только FREE)",
            when_wheel, do_wheel, reward_kind="items", cooldown=300.0))

        def when_pets(hud, scene, ctx):
            return time.time() - ctx.get("last_pets", 0) > 1200.0

        def do_pets(ctx):
            ok1 = self.pets.equip_best()
            deleted = self.pets.delete_weakest(max_deletes=3)
            ctx["last_pets"] = time.time()
            return bool(ok1 or deleted)

        R.register(Capability(
            "PETS_MAINTENANCE", "Экипировать лучших питомцев и почистить слабых",
            when_pets, do_pets, reward_kind="strength", cooldown=600.0))

        def when_eggs(hud, scene, ctx):
            return time.time() - ctx.get("last_egg", 0) > 1200.0

        def do_eggs(ctx):
            ok = self.eggs.hatch_egg(ctx.get("scene"))
            ctx["last_egg"] = time.time()
            return ok

        R.register(Capability(
            "HATCH_EGG", "Найти стенд яиц и открыть яйцо (выше пола гемов)",
            when_eggs, do_eggs, reward_kind="items", cooldown=600.0))

        def when_train(hud, scene, ctx):
            return True  # training is always possible

        def _train(ctx, lifts, equip):
            """Runs a verified set and records whether the gain was MEASURED.

            success is True for both 'gain measured' and 'could not measure':
            the reps were physically executed, so the capability is not broken.
            The difference is exposed via ctx['_last_verified'], which the Mind
            forwards to the registry, so unverified sets never inflate the
            success rate that drives the UCB choice.
            """
            gain = self.a.smart_lift(lifts=lifts, equip=equip)
            ctx["_last_verified"] = gain is not None
            return gain is None or gain > 0

        def do_train_dumbbells(ctx):
            return _train(ctx, 6, "dumbbells")

        def do_train_pushups(ctx):
            return _train(ctx, 6, "pushups")

        def do_train_situps(ctx):
            return _train(ctx, 6, "situps")

        def do_train_machine(ctx):
            if self.nav.find_machine(max_total=8.0, scene=ctx.get("scene")):
                return self.a.machine_session(reps=8) != "failed"
            return _train(ctx, 4, "dumbbells")

        def do_train_agility(ctx):
            if self.nav.find_machine(max_total=6.0, scene=ctx.get("scene")):
                return self.a.machine_session() != "failed"
            self.a.train_agility_run(duration_sec=3.5)
            return True

        R.register(Capability(
            "TRAIN_DUMBBELLS", "Жим гантелей (Slot 2) - основной источник силы",
            when_train, do_train_dumbbells, reward_kind="strength", cooldown=0.0))
        R.register(Capability(
            "TRAIN_PUSHUPS", "Отжимания (Slot 7)",
            when_train, do_train_pushups, reward_kind="strength", cooldown=0.0))
        R.register(Capability(
            "TRAIN_SITUPS", "Приседания (Slot 8)",
            when_train, do_train_situps, reward_kind="strength", cooldown=0.0))
        R.register(Capability(
            "TRAIN_MACHINE", "Тренажёр (E): жим/дедлифт - высокий множитель",
            when_train, do_train_machine, reward_kind="strength", cooldown=0.0))
        R.register(Capability(
            "TRAIN_AGILITY", "Беговая дорожка (E) или спринт",
            when_train, do_train_agility, reward_kind="strength", cooldown=0.0))

        def when_discover(hud, scene, ctx):
            return time.time() - ctx.get("last_discover", 0) > 420.0

        def do_discover(ctx):
            name = self.nav.discover(seconds=8.0, scene=ctx.get("scene"))
            ctx["last_discover"] = time.time()
            if name and name != "unknown":
                # SELF-GROWTH: a new interactable becomes a new capability
                cap_name = f"USE_{name.upper()[:24]}"
                if cap_name not in self.registry.caps:
                    def do_use(c=ctx, n=name):
                        if self.nav.find_machine(max_total=6.0, scene=c.get("scene")):
                            return self.a.machine_session(reps=6) != "failed"
                        return False
                    self.registry.add_dynamic(
                        cap_name, f"Использовать найденный объект: {name}",
                        lambda hud, scene, ctx: not (set(scene.get("events", [])) & {"boss"}), do_use,
                        reward_kind="strength")
                return True
            return False

        R.register(Capability(
            "DISCOVER", "Разведка: искать НОВЫЕ тренажёры/стенды/яйца (адаптация)",
            when_discover, do_discover, reward_kind="info", cooldown=300.0))

        def do_daily(ctx):
            """Открывает панель ежедневных наград (кнопка ITEMS), забирает
            доступные CLAIM-дни, закрывает. Ничего не покупает."""
            if not self.a.wm.ensure_focused():
                return False
            self.a.input.click_roi("items_btn", self.a.wm)
            time.sleep(1.2)
            lines, frame = self.a._modal_lines("daily_panel")
            joined = " ".join((l.get("text") or "") for l in lines)
            nj = joined.upper().translate(CYRILLIC_MAP)
            if "DAILY" not in nj and "REWARD" not in nj:
                print("[DAILY] панель не открылась/другая - закрыть.")
                self.a.close_modal_safe()
                return False
            claimed = 0
            for l in lines:
                if self.a.ocr.looks_like_claim(l.get("text")):
                    pt = self.a._line_to_screen("modal_area", l, frame)
                    if pt:
                        self.a.input.mouse_click(pt[0], pt[1])
                        claimed += 1
                        time.sleep(0.8)
            print(f"[DAILY] claimed {claimed} reward(s).")
            self.a.close_modal_safe()
            ctx["last_daily"] = time.time()
            return claimed > 0

        def when_daily(hud, scene, ctx):
            return time.time() - ctx.get("last_daily", 0) > 3600.0

        R.register(Capability(
            "DAILY_REWARDS", "Ежедневные награды: забрать доступные CLAIM-дни",
            when_daily, do_daily, reward_kind="items", cooldown=1800.0))

        def when_rebirth(hud, scene, ctx):
            from config import FEATURE_REBIRTH
            if not FEATURE_REBIRTH:
                return False
            strength = hud.get("strength", 0)
            rebirths = hud.get("rebirths", 0)
            from config import get_rebirth_requirement
            req = get_rebirth_requirement(rebirths)
            return req > 0 and strength >= req

        def do_rebirth(ctx):
            return self.rebirth.attempt()

        R.register(Capability(
            "REBIRTH", "Ребёрн (бесплатная механика, верификация на экране)",
            when_rebirth, do_rebirth, reward_kind="strength", cooldown=600.0))

        # danger-aware: boss danger disables walking capabilities
        for name in ("TRAIN_MACHINE", "TRAIN_AGILITY", "DISCOVER"):
            cap = self.registry.caps[name]
            base_when = cap.when
            cap.when = lambda hud, scene, ctx, b=base_when, n=name: (
                not (set(scene.get("events", [])) & {"boss"})) and b(hud, scene, ctx)

        self.registry.save()

    # ================================================================
    # DECIDE / EXECUTE / FEEDBACK
    # ================================================================

    def decide(self, hud, scene, ctx) -> Optional[Capability]:
        ranked = self.registry.ranked(hud, scene, ctx)
        if not ranked:
            return None
        return ranked[0][1]

    def execute(self, cap: Capability, hud, scene, ctx) -> Dict:
        """Runs the capability, measures the reward, feeds back."""
        strength_before = self.a.read_strength() if cap.reward_kind == "strength" else None
        gems_before = self.a.read_gems() if cap.reward_kind in ("gems", "items") else None

        t0 = time.time()
        error = ""
        success = False
        ctx.pop("_last_verified", None)
        try:
            success = bool(cap.execute(ctx))
        except Exception as e:
            error = str(e)
            success = False
        cost = time.time() - t0
        # True unless the capability explicitly reported that its result could
        # not be measured (unreadable badge) - those samples must not be used
        # as evidence of success when ranking capabilities.
        verified = bool(ctx.pop("_last_verified", True))

        reward = 0.0
        detail = error or ("ok" if success else "no result")
        if cap.reward_kind == "strength" and not error:
            strength_after = self.a.read_strength()
            if strength_before is not None and strength_after is not None:
                reward = max(0.0, (strength_after - strength_before) / 1000.0)  # K units
                detail = f"strength +{reward:.1f}K"
                verified = True
        elif cap.reward_kind in ("gems", "items") and not error:
            gems_after = self.a.read_gems()
            if gems_before is not None and gems_after is not None:
                d = gems_after - gems_before
                reward = max(0.0, d / 10000.0)
                detail = f"gems {d:+,}"

        self.registry.feedback(cap.name, success, reward, cost, detail,
                               verified=verified)
        self.pending_reward_ctx = None
        return {"success": success, "reward": reward, "cost": cost,
                "detail": detail, "verified": verified}

    # ================================================================
    # REFLECTION (self-rewritten instruction)
    # ================================================================

    def remember_context(self, hud, scene, ctx):
        """Keeps the last context so reflect() can evaluate availability."""
        self._last_hud = hud
        self._last_scene = scene
        self._last_ctx = ctx

    def _reflect_eye_health(self):
        """Folds the eye's own health into the agent's learned insights.

        Self-development is only as good as the evidence behind it: if the eye
        is blind on a badge, every reward sampled from it is unknown, so the
        agent must notice that instead of silently optimizing a stale prior.
        Uses the LAST scene (free) - reflection runs on a 10-minute timer and
        must never stall the bot for a fresh capture + OCR.
        """
        try:
            perceive = getattr(self.a, "perceiver", None)
            if perceive is None:
                return
            glyphs = perceive.glyph_health()
            scene = perceive.last_scene or {}
            quality = scene.get("quality", {})
            problems = []
            if glyphs.get("missing"):
                problems.append("no glyph templates for "
                                f"{glyphs['missing']} (those counters cannot be read)")
            hud = scene.get("hud", {})
            for kind in ("strength", "gems"):
                entry = hud.get(kind) or {}
                if entry.get("stale"):
                    problems.append(f"{kind} badge unreadable for "
                                    f"{entry.get('age')}s")
            if scene and not quality.get("game_visible", True):
                problems.append("captured frame had no game HUD "
                                "(Roblox occluded/minimized)")
            for text in problems:
                self.memory.add_insight(f"eye: {text}")
            if problems:
                print("[MIND] eye health: " + "; ".join(problems))
        except Exception as exc:
            print(f"[MIND] eye health check skipped: {exc}")

    def reflect(self, force: bool = False):
        """Periodically rewrites its own playbook + saves insights."""
        now = time.time()
        if not force and now - self.last_reflection < 600.0:
            return
        self.last_reflection = now

        self._reflect_eye_health()
        playbook = self.registry.playbook_text(hud=self._last_hud or {}, scene=self._last_scene or {}, ctx=self._last_ctx or {})
        PLAYBOOK_TXT.write_text(
            playbook + "\n\nОбновлено: " + time.strftime("%Y-%m-%d %H:%M") + "\n",
            encoding="utf-8")

        caps_data = {name: c.to_dict() for name, c in self.registry.caps.items()}
        PLAYBOOK_FILE.write_text(json.dumps({
            "updated": now,
            "policy": caps_data,
            "notes": self.memory.data.get("learned_insights", [])[-10:],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print("[MIND] playbook self-updated (data/playbook.txt).")

    def status_line(self) -> str:
        caps = self.registry.caps
        tried = sum(1 for c in caps.values() if c.attempts)
        return f"capabilities={len(caps)} (tried {tried})"
