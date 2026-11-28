#!/usr/bin/env python3
"""
Генератор V4: дополнительные клипы анимаций для 3D Desktop Pet.

Расширяет tools/build_pet_clips.py, не изменяя его: импортирует все хелперы
и базовые клипы, добавляет V4-блок (16 новых клипов) и пишет обновлённый
alontito/pet3d/animations.json.

Ревью пользователя 28.11 — переписаны с нуля: wave (ладонь к экрану, рука
мимо капюшона), backflip (настоящее вращение на 360° вокруг поперечной оси),
laugh (руки к груди, откид назад, дрожь смеха), dance (классика Roblox),
idea (рука с «пальцем вверх», ладонь вбок). nod оставлен — кивает правильно.
Эффекты 😂/💡 делает pet.js (FX_BY_CLIP).

Запуск (из корня репозитория):
  python tools/build_pet_clips_v4.py
"""
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("bpc", ROOT / "tools" / "build_pet_clips.py")
bpc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpc)          # наполняет bpc.CLIPS и хелперы

S, C, rad, A, R, AB = bpc.S, bpc.C, bpc.rad, bpc.A, bpc.R, bpc.AB
merge, key, sample = bpc.merge, bpc.key, bpc.sample
arm, arm_dir, leg, legs_sit = bpc.arm, bpc.arm_dir, bpc.leg, bpc.legs_sit
clip = bpc.clip
CLIPS = bpc.CLIPS

def AR(x, y, z, roll=0.0):
    """aim + доверот кости вокруг её оси (roll, °): управляет ладонью/плоскостью кисти."""
    n = math.sqrt(x*x + y*y + z*z) or 1.0
    d = {"aim": [round(x/n, 4), round(y/n, 4), round(z/n, 4)]}
    if roll:
        d["roll"] = round(roll, 1)
    return d

def arm_dir_r(side, up, fo, hand=None):
    """arm_dir с роллом: каждый вектор — (x, y, z[, roll]). Для L z зеркалится."""
    def conv(v):
        return AR(v[0], v[1], (-v[2] if side == "L" else v[2]), v[3] if len(v) > 3 else 0.0)
    hand = fo if hand is None else hand
    return {f"{side}_Upperarm": conv(up), f"{side}_Forearm": conv(fo),
            f"{side}_Hand": conv(hand)}

# ---------------------------------------------------------------- wave
# Рука поднята в сторону, машет в наружной дуге мимо капюшона; ладонь развёрнута
# к зрителю (roll). Голова чуть откинута от руки, корпус слегка наружу.
def f_wave(p):
    w = 2*math.pi*p
    bones = merge(
        arm_dir_r("R", (0.42, 0.72, 0.80, 90), (0.16, 0.86, 0.62, 90), (0.10, 0.80, 0.72, 90)),
        arm_dir_r("L", (0.10, -0.96, 0.24), (0.14, -0.98, 0.16)),
        leg("R", 1.5, 2.0), leg("L", -1.5, 2.0),
        {"R_Clavicle": A(0.0, 0.42, 1.0), "L_Clavicle": A(0.0, -0.18, -1.0),
         "Spine01": R(("z", -3.0)), "Head": R(("z", -9.0), ("y", 10*S(w))),
         "Waist": R(("z", -2.0))},
    )
    bones["R_Forearm"] = AR(0.10 + 0.22*S(3*w), 0.84, 0.72 + 0.14*C(3*w), 90)
    bones["R_Hand"]    = AR(0.08 + 0.30*S(3*w + 0.6), 0.80, 0.72, 90)
    return bones, (0.0, 0.004*S(2*w), 0.0)
clip("wave", 1.6, False, sample(1.6, 16, f_wave))

# ---------------------------------------------------------------- nod
# Кивок ВПЕРЁД (голова по оси x наклоняется вниз и возвращается) — не качание.
clip("nod", 1.1, False, [
    key(0.00, merge(bpc.stand(), {"Head": R(("x", 0.0))}), (0, 0, 0)),
    key(0.25, merge(bpc.stand(), {"Head": R(("x", 18.0)), "Spine02": R(("x", 4.0))}), (0, -0.01, 0)),
    key(0.50, merge(bpc.stand(), {"Head": R(("x", -6.0))}), (0, 0.005, 0)),
    key(0.75, merge(bpc.stand(), {"Head": R(("x", 14.0)), "Spine02": R(("x", 3.0))}), (0, -0.008, 0)),
    key(1.10, merge(bpc.stand(), {"Head": R(("x", 0.0))}), (0, 0, 0)),
])

# ---------------------------------------------------------------- shake_head
clip("shake_head", 1.3, False, [
    key(0.00, merge(bpc.stand(), {"Head": R(("y", 0.0))}), (0, 0, 0)),
    key(0.20, merge(bpc.stand(), {"Head": R(("y", -24.0)), "Spine01": R(("y", -4.0))}), (0, 0, 0)),
    key(0.45, merge(bpc.stand(), {"Head": R(("y", 22.0)), "Spine01": R(("y", 4.0))}), (0, 0, 0)),
    key(0.70, merge(bpc.stand(), {"Head": R(("y", -20.0)), "Spine01": R(("y", -3.0))}), (0, 0, 0)),
    key(0.95, merge(bpc.stand(), {"Head": R(("y", 8.0))}), (0, 0, 0)),
    key(1.30, merge(bpc.stand(), {"Head": R(("y", 0.0))}), (0, 0, 0)),
])

# ---------------------------------------------------------------- sleepy
def f_sleepy(p):
    w = 2*math.pi*p
    bones = merge(
        legs_sit(0.16, 0.42),                 # сидит собравшись
        {"Waist": R(("z", -2.0)),
         "Spine01": R(("z", -12.0 + 1.0*S(w)), ("x", 4.0)),
         "Spine02": R(("z", -6.0)),
         "Head": R(("x", 26.0 + 4*S(w*0.5)), ("z", 6.0), ("y", 6*S(w*0.25)))},
        arm_dir("R", (0.30, -0.80, 0.42), (0.80, -0.55, 0.25)),   # руки «обнял себя»
        arm_dir("L", (0.30, -0.80, 0.42), (0.80, -0.55, 0.25)),
        {"R_Clavicle": A(0.0, -0.1, 1.0), "L_Clavicle": A(0.0, -0.1, -1.0)},
    )
    return bones, (0.0, -0.225 + 0.006*S(w), 0.0)
clip("sleepy", 5.0, True, sample(5.0, 25, f_sleepy))

# ---------------------------------------------------------------- fall
clip("fall", 0.9, False, [
    key(0.00, bpc.stand(4, 12, 9), (0, 0, 0)),
    key(0.25, merge(bpc.stand(30, 30, 40), {"Head": R(("x", -12.0)), "Spine01": R(("x", -8.0))}), (0, 0.12, 0)),
    key(0.55, merge(bpc.stand(55, 40, 60), {"Head": R(("x", -18.0)), "Spine01": R(("x", -14.0))}), (0, 0.30, 0)),
    key(0.75, merge(bpc.stand(75, 45, 70), {"Head": R(("x", -20.0))}), (0, 0.05, 0)),
    key(0.90, bpc.crouch if hasattr(bpc, "crouch") else merge(leg("R", 42, 82, 12), leg("L", 42, 82, 12), bpc.stand(-10, 55, 5)), (0, -0.12, 0)),
])

# ---------------------------------------------------------------- land
clip("land", 0.8, False, [
    key(0.00, merge(leg("R", 42, 82, 12), leg("L", 42, 82, 12),
                    arm("R", -10, 55, 5), arm("L", -10, 55, 5),
                    {"Spine01": R(("z", -18.0)), "Head": R(("z", 10.0)),
                     "R_Clavicle": A(0.0, 0.1, 1.0), "L_Clavicle": A(0.0, 0.1, -1.0)}), (0, -0.12, 0)),
    key(0.25, merge(leg("R", 30, 55, 10), leg("L", 30, 55, 10),
                    arm("R", -5, 35, 12), arm("L", -5, 35, 12),
                    {"Spine01": R(("z", -10.0))}), (0, -0.05, 0)),
    key(0.55, merge(bpc.stand(2, 14, 8), {"Spine01": R(("z", 3.0))}), (0, 0.01, 0)),
    key(0.80, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- backflip
# НАСТОЯЩЕЕ сальто: Hip получает АБСОЛЮТНОЕ вращение -360° вокруг поперечной
# оси персонажа (z модели) — корпус проворачивается целиком; прыжок даёт
# смещение бедра по y. Группировка: колени к груди, руки к коленям, подбородок вниз.
def fkey(t, z_deg, hipy, head_x, spine_x, tuck):
    legs = legs_sit(0.20, 0.55) if tuck else merge(leg("R", 6, 12, 7), leg("L", -6, 12, 7))
    arms = (arm_dir_r("R", (0.12, 0.45, 0.90, 0), (0.30, 0.88, 0.62, 0)),
            arm_dir_r("L", (0.12, 0.45, 0.90, 0), (0.30, 0.88, 0.62, 0))) if tuck else \
           (arm_dir("R", (0.10, -0.92, 0.32), (0.28, -0.82, 0.40)),
            arm_dir("L", (0.10, -0.92, 0.32), (0.28, -0.82, 0.40)))
    return key(t, merge(
        {"Hip": AB(("z", z_deg)),
         "Spine01": R(("x", spine_x), ("z", -2.0)),
         "Head": R(("x", head_x), ("z", 2.0)),
         "R_Clavicle": A(0.0, 0.1, 1.0), "L_Clavicle": A(0.0, 0.1, -1.0)},
        *arms, legs,
    ), (0.0, hipy, 0.0))
clip("backflip", 1.7, False, [
    fkey(0.00, 0, -0.10, 0, 0, False),        # стойка
    fkey(0.22, 0, -0.20, 6, 10, False),       # присед-замах
    fkey(0.45, -100, 0.30, -12, -10, True),   # отрыв, наклон назад, группировка
    fkey(0.72, -200, 0.34, -14, -12, True),   # вверх ногами (группировка)
    fkey(0.98, -300, 0.16, 8, 6, False),      # выход из вращения
    fkey(1.22, -360, -0.14, 0, 0, False),     # приземление в присед
    fkey(1.70, -360, 0.0, 0, 0, False),       # выпрямился
], meta={"fx": "spin"})

# ---------------------------------------------------------------- spin_360
def f_spin(p):
    w = 2*math.pi*p
    yaw = 360.0 * p
    # руки прижаты к корпусу и чуть согнуты: раньше разведённые руки
    # выметали обе кисти сквозь голову (d до 0.03 м при повороте корпуса)
    bones = merge(
        arm_dir("R", (0.12, -0.92, 0.34), (0.30, -0.80, 0.42)),
        arm_dir("L", (0.12, -0.92, 0.34), (0.30, -0.80, 0.42)),
        leg("R", 4, 6, 8), leg("L", -4, 6, 8),
        {"Waist": R(("y", yaw)), "Spine01": R(("y", yaw*0.3), ("z", 4.0)),
         "Head": R(("y", yaw*0.2), ("z", 3.0))},
    )
    return bones, (0.0, 0.02*abs(S(w)) + 0.01, 0.0)
spin_keys = sample(0.9, 18, f_spin)
spin_keys[-1]["b"] = spin_keys[0]["b"]                    # замыкаем позу
spin_keys[-1]["hip"] = spin_keys[0]["hip"]
clip("spin_360", 0.9, False, spin_keys, meta={"fx": "spin"})

# ---------------------------------------------------------------- moonwalk
def f_moon(p):
    w = 2*math.pi*p
    slide = max(0.0, -S(w))**1.5                           # скольжение назад
    bones = merge(
        leg("R", 20*S(w), 30 + 35*max(0.0, C(w)), 6),
        leg("L", -20*S(w), 30 + 35*max(0.0, -C(w)), 6),
        arm_dir("R", (0.2, 0.55, 0.85), (0.3, 0.8, 0.45)),
        arm_dir("L", (0.2, 0.55, 0.85), (0.3, 0.8, 0.45)),
        {"Waist": R(("y", 10*S(w*0.5)), ("z", -8.0)),
         "Spine01": R(("y", 14*S(w)), ("z", -5.0)),
         "Head": R(("y", -18*S(w)), ("z", 8.0))},
    )
    return bones, (0.0, -0.02*slide + 0.012*abs(C(w)), -0.03*slide)
clip("moonwalk", 1.6, True, sample(1.6, 24, f_moon), ease="linear")

# ---------------------------------------------------------------- stretch
# Потягивание: руки поднимаются ШИРОКО в стороны (не над головой), спина
# прогибается назад — раньше кисти уходили в капюшон (d до 0.12)
clip("stretch", 2.2, False, [
    key(0.00, bpc.stand(), (0, 0, 0)),
    key(0.40, merge(arm_dir("R", (0.14, 0.72, 0.78), (0.18, 0.76, 0.70)),
                    arm_dir("L", (0.14, 0.72, 0.78), (0.18, 0.76, 0.70)),
                    leg("R", 2, 3, 6), leg("L", -2, 3, 6),
                    {"Spine01": R(("x", -10.0), ("z", 2.0)), "Head": R(("x", -8.0))}), (0, 0.03, 0)),
    key(1.00, merge(arm_dir("R", (0.10, 0.76, 0.74), (0.14, 0.80, 0.64)),
                    arm_dir("L", (0.10, 0.76, 0.74), (0.14, 0.80, 0.64)),
                    leg("R", 3, 3, 6), leg("L", -3, 3, 6),
                    {"Spine01": R(("x", -14.0), ("z", 4.0)), "Spine02": R(("x", -6.0)),
                     "Head": R(("x", -10.0))}), (0, 0.045, 0)),
    key(1.60, merge(arm_dir("R", (0.18, 0.68, 0.80), (0.26, 0.74, 0.66)),
                    arm_dir("L", (0.18, 0.68, 0.80), (0.26, 0.74, 0.66)),
                    leg("R", 2, 3, 6), leg("L", -2, 3, 6),
                    {"Spine01": R(("x", -6.0))}), (0, 0.02, 0)),
    key(2.20, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- yawn
# Зевок с потягиванием: руки шире (наружу от капюшона), голова запрокинута
clip("yawn", 2.0, False, [
    key(0.00, bpc.stand(), (0, 0, 0)),
    key(0.45, merge(arm_dir("R", (0.2, 0.66, 0.80), (0.3, 0.74, 0.64)),
                    arm_dir("L", (0.2, 0.66, 0.80), (0.3, 0.74, 0.64)),
                    leg("R", 1.5, 2.0), leg("L", -1.5, 2.0),
                    {"Spine01": R(("x", -8.0)), "Head": R(("x", -18.0))}), (0, 0.01, 0)),
    key(1.00, merge(arm_dir("R", (0.1, 0.70, 0.78), (0.2, 0.76, 0.66)),
                    arm_dir("L", (0.1, 0.70, 0.78), (0.2, 0.76, 0.66)),
                    leg("R", 2, 3, 6), leg("L", -3, 3, 6),
                    {"Spine01": R(("x", -12.0)), "Head": R(("x", -22.0))}), (0, 0.015, 0)),
    key(1.50, merge(bpc.stand(), {"Head": R(("x", 5.0))}), (0, 0, 0)),
    key(2.00, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- think
def f_think(p):
    w = 2*math.pi*p
    bones = merge(
        legs_sit(0.20, 0.52),
        arm_dir("R", (0.45, -0.78, 0.42), (0.70, -0.55, 0.45), (0.55, 0.15, 0.75)),  # кулак у подбородка
        arm_dir("L", (0.35, -0.88, 0.30), (0.85, -0.40, 0.20)),
        {"Waist": R(("z", -4.0)),
         "Spine01": R(("z", -5.0)),
         "Head": R(("z", 14.0), ("y", 10*S(w*0.5)), ("x", 6.0))},
        {"R_Clavicle": A(0.0, -0.05, 1.0), "L_Clavicle": A(0.0, -0.2, -1.0)},
    )
    return bones, (0.0, -0.225, 0.0)
clip("think", 3.2, True, sample(3.2, 16, f_think))

# ---------------------------------------------------------------- idea
# «Есть идея!»: правая рука вскидывается вверх, ладонь развёрнута в сторону
# (roll), голова поднимается за рукой; 💡 дорисовывает pet.js.
clip("idea", 1.4, False, [
    key(0.00, bpc.stand(), (0, 0, 0)),
    key(0.22, merge(bpc.stand(), {"Head": R(("z", -5.0), ("x", -4.0)),
                                   "Spine01": R(("z", -2.0))}), (0, 0, 0)),
    key(0.50, merge(
        arm_dir_r("R", (0.15, 0.88, 0.55, 90), (0.10, 0.94, 0.44, 90), (0.05, 0.96, 0.40, 90)),
        arm_dir("L", (0.12, -0.94, 0.28), (0.20, -0.92, 0.40)),
        leg("R", 2, 3, 6), leg("L", -2, 3, 6),
        {"Spine01": R(("x", -4.0), ("z", -2.0)), "Head": R(("x", -10.0), ("z", -6.0))},
    ), (0, 0.02, 0)),
    key(0.85, merge(
        arm_dir_r("R", (0.15, 0.93, 0.50, 90), (0.10, 0.97, 0.40, 90), (0.05, 0.98, 0.36, 90)),
        arm_dir("L", (0.12, -0.94, 0.28), (0.20, -0.92, 0.40)),
        leg("R", 2, 3, 6), leg("L", -2, 3, 6),
        {"Spine01": R(("x", -7.0), ("z", -2.0)), "Head": R(("x", -14.0), ("z", -6.0))},
    ), (0, 0.035, 0)),
    key(1.40, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- dab
clip("dab", 1.4, False, [
    key(0.00, bpc.stand(), (0, 0, 0)),
    key(0.30, merge(arm_dir("R", (0.55, 0.75, 0.35), (0.90, 0.35, 0.20)),   # правая вверх-вбок
                    arm_dir("L", (0.75, 0.35, -0.55), (0.95, 0.05, -0.25)),  # левая через лицо
                    leg("R", 4, 6, 8), leg("L", -6, 6, 8),
                    {"Waist": R(("y", -8.0)), "Spine01": R(("y", -10.0), ("z", 3.0)),
                     "Head": R(("y", -18.0), ("z", 6.0))}), (0, 0.02, 0)),
    key(0.80, merge(arm_dir("R", (0.55, 0.75, 0.35), (0.90, 0.35, 0.20)),
                    arm_dir("L", (0.75, 0.35, -0.55), (0.95, 0.05, -0.25)),
                    leg("R", 4, 6, 8), leg("L", -6, 6, 8),
                    {"Waist": R(("y", -8.0)), "Spine01": R(("y", -10.0), ("z", 3.0)),
                     "Head": R(("y", -18.0), ("z", 6.0))}), (0, 0.02, 0)),
    key(1.15, bpc.stand(2, 14, 8), (0, 0, 0)),
    key(1.40, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- peek
clip("peek", 1.8, False, [
    key(0.00, merge(leg("R", 50, 95, 14), leg("L", 50, 95, 14),
                    arm("R", -8, 40, 10), arm("L", -8, 40, 10),
                    {"Spine01": R(("z", -14.0)), "Head": R(("z", 8.0), ("y", 0.0)),
                     "Waist": R(("y", 0.0))}), (0, -0.16, 0)),
    key(0.45, merge(leg("R", 50, 95, 14), leg("L", 50, 95, 14),
                    arm("R", -8, 40, 10), arm("L", -8, 40, 10),
                    {"Spine01": R(("z", -14.0)), "Head": R(("z", 8.0), ("y", 30.0)),
                     "Waist": R(("y", 0.0))}), (0, -0.16, 0)),
    key(0.75, merge(leg("R", 50, 95, 14), leg("L", 50, 95, 14),
                    arm("R", -8, 40, 10), arm("L", -8, 40, 10),
                    {"Spine01": R(("z", -14.0)), "Head": R(("z", 8.0), ("y", -28.0)),
                     "Waist": R(("y", 0.0))}), (0, -0.16, 0)),
    key(1.10, merge(leg("R", 50, 95, 14), leg("L", 50, 95, 14),
                    arm("R", -8, 40, 10), arm("L", -8, 40, 10),
                    {"Spine01": R(("z", -14.0)), "Head": R(("z", 8.0), ("y", 0.0)),
                     "Waist": R(("y", 0.0))}), (0, -0.16, 0)),
    key(1.55, merge(bpc.stand(0, 14, 8)), (0, 0, 0)),
    key(1.80, bpc.stand(), (0, 0, 0)),
])

# ---------------------------------------------------------------- headbang
def f_bang(p):
    w = 2*math.pi*p
    hit = max(0.0, S(2*w))**3                          # удары
    bones = merge(
        arm_dir("R", (0.15, 0.55, 0.82), (0.25, 0.85, 0.45)),
        arm_dir("L", (0.15, 0.55, 0.82), (0.25, 0.85, 0.45)),
        leg("R", 6, 8, 10), leg("L", -6, 8, 10),
        {"Waist": R(("x", 10*hit)), "Spine01": R(("x", 14*hit), ("z", 3*S(w))),
         "Head": R(("x", 26*hit - 6), ("z", 5*S(2*w)))},
    )
    return bones, (0.0, -0.01*hit, 0.0)
clip("headbang", 1.6, True, sample(1.6, 24, f_bang), ease="linear")

# ---------------------------------------------------------------- laugh
# Смех: руки к груди, корпус откидывается назад и встряхивается тремя тактами,
# голова запрокинута, колени пружинят; 😂 рисует pet.js.
def f_laugh(p):
    w = 2*math.pi*p
    beat = abs(S(w*3))**0.7                            # три такта смеха за клип
    bones = merge(
        {"Waist": R(("z", -2.0)),
         "Spine01": R(("x", 6.0 + 7.0*beat), ("z", -3.0)),
         "Spine02": R(("x", 4.0*beat)),
         "Head": R(("x", -14.0 - 9.0*beat), ("z", 4.0)),
         "R_Clavicle": A(0.0, 0.12, 1.0), "L_Clavicle": A(0.0, 0.12, -1.0)},
        # руки сложены к груди: локти вниз-вперёд, кисти сходятся у груди
        arm_dir("R", (0.28, -0.78, 0.55), (0.85, -0.15, 0.25), (0.92, 0.18, -0.18)),
        arm_dir("L", (0.28, -0.78, 0.55), (0.85, -0.15, 0.25), (0.92, 0.18, 0.18)),
        leg("R", 2 + 2*beat, 4 + 3*beat, 6), leg("L", -2, 4 + 3*beat, 6),
    )
    return bones, (0.0, -0.012*beat, 0.0)
clip("laugh", 2.6, False, sample(2.6, 26, f_laugh))

# ---------------------------------------------------------------- dance
# Классический танец Roblox: руки горизонтально в стороны и машут в
# противофазе, бёдра качаются в такт, колени пружинят, голова боббит.
def f_dance(p):
    w = 2*math.pi*p
    sway = S(w*2)                                      # качание на каждый бит
    bounce = abs(S(w*4))**0.8                          # пружинка коленей
    bones = merge(
        {"Waist": R(("z", 5.0*sway)),
         "Spine01": R(("z", 7.0*sway), ("y", 4.0*S(w))),
         "Head": R(("z", -6.0*sway), ("x", -5.0 - 4.0*bounce), ("y", 7.0*S(w*2))),
         "R_Clavicle": A(0.0, 0.35, 1.0), "L_Clavicle": A(0.0, 0.35, -1.0)},
        leg("R", 3 + 3*sway, 10 + 14*bounce, 10),
        leg("L", -3 + 3*sway, 10 + 14*bounce, 10),
        # руки в стороны, каждая машет вверх-вниз в противофазе
        arm_dir("R", (0.0, 0.30 + 0.55*C(w*2), 1.0), (0.0, 0.55 + 0.35*C(w*2 + math.pi), 0.95)),
        arm_dir("L", (0.0, 0.30 + 0.55*S(w*2), 1.0), (0.0, 0.55 + 0.35*S(w*2 + math.pi), 0.95)),
    )
    return bones, (0.025*sway, -0.014*bounce, 0.0)
clip("dance", 3.2, True, sample(3.2, 32, f_dance), ease="linear")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "alontito" / "pet3d" / "animations.json"
    data = {"version": 4, "fps": 30, "driven": [
        "Hip", "Waist", "Spine01", "Spine02", "R_Clavicle", "L_Clavicle",
        "R_Upperarm", "L_Upperarm", "R_Forearm", "L_Forearm", "R_Hand", "L_Hand",
        "Head", "R_Thigh", "L_Thigh", "R_Calf", "L_Calf", "R_Foot", "L_Foot"],
        "clips": CLIPS}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    rewritten = ["wave", "backflip", "laugh", "dance", "idea", "nod"]
    print(f"OK: {len(CLIPS)} clips -> {out} ({out.stat().st_size} bytes)")
    print("rewritten by user review:", ", ".join(n for n in rewritten if n in CLIPS))

if __name__ == "__main__":
    main()
