"""Regenerate the charts in docs/images from real pipeline output (pandas reference path).

    python scripts/make_readme_assets.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sunops import demo, reference  # noqa: E402
from sunops.contracts import load_contract  # noqa: E402
from sunops.generator import generate  # noqa: E402

OUT = ROOT / "docs" / "images"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
ZONE = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}  # categorical slots 1-3
STATUS = {"CLEAN_NOW": "#d03b3b", "SCHEDULE": "#fab219", "INSPECT": "#ec835a", "OK": "#0ca30c"}
WEATHER_FR = {"chergui": "Chergui", "sandstorm": "Tempête de sable", "cloudy": "Nuageux"}

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK2,
        "axes.titlecolor": INK,
        "axes.titleweight": "bold",
        "axes.titlesize": 13,
        "axes.titlelocation": "left",
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "font.size": 10,
        "font.family": "DejaVu Sans",
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def build(days: int = 14):
    contract = load_contract(ROOT / "contracts" / "telemetry.yml")
    with tempfile.TemporaryDirectory() as tmp:
        stats = generate(date(2026, 3, 2), days, Path(tmp), seed=42, chaos=0.01)
        records = reference.read_bronze(Path(tmp))
    silver, quarantine = reference.bronze_to_silver(records, contract)
    daily = reference.daily_performance(silver)
    reco = reference.cleaning_recommendations(daily)
    return stats, silver, daily, reco


def chart_telemetry(stats, silver, daily) -> None:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.2), gridspec_kw={"height_ratios": [1, 1.1], "hspace": 0.45})

    # 1. one array per zone, first four days
    days = sorted(stats.weather)[:4]
    window = silver[silver["date_day"].astype(str).isin(days)]
    for zone, array in (("A", "A-INV1-S1"), ("C", "C-INV1-S1")):
        s = window[window["array_id"] == array].sort_values("ts")
        ax1.plot(s["ts"], s["ac_power_kw"], color=ZONE[zone], lw=2, label=f"{array} (zone {zone})")
    for d in days:
        w = stats.weather[d]
        if w in WEATHER_FR:
            start = pd.Timestamp(d, tz="UTC")
            ax1.axvspan(start, start + pd.Timedelta(days=1), color="#efeeea", zorder=0, lw=0)
            ax1.text(start + pd.Timedelta(hours=12), 232, WEATHER_FR[w], ha="center", color=INK2, fontsize=9)
    ax1.set_ylim(0, 250)
    ax1.set_ylabel("Puissance AC (kW)")
    ax1.set_title("Télémétrie simulée : 4 jours, une lecture toutes les 15 minutes")
    ax1.legend(loc="upper left", frameon=False, ncol=2, bbox_to_anchor=(0, -0.12))
    ax1.xaxis.set_major_locator(matplotlib.dates.DayLocator())
    ax1.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d/%m"))

    # 2. daily performance ratio per zone
    pr = daily.groupby(["date_day", "zone"]).apply(
        lambda g: g["energy_kwh"].sum() / g["reference_kwh"].sum(), include_groups=False
    ).unstack()
    x = pd.to_datetime(pr.index)
    nudge = {"A": -5, "B": 5, "C": 0}
    for zone in "ABC":
        ax2.plot(x, pr[zone], color=ZONE[zone], lw=2, marker="o", ms=5,
                 markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax2.annotate(f"Zone {zone}", (x[-1], pr[zone].iloc[-1]), xytext=(8, nudge[zone]), textcoords="offset points",
                     va="center", color=INK, fontsize=10)
    ax2.set_title("Performance Ratio par zone : le robot de la zone C est en panne")
    ax2.set_ylabel("Performance Ratio")
    ax2.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d/%m"))
    ax2.set_xlim(x[0] - pd.Timedelta(hours=12), x[-1] + pd.Timedelta(days=1.3))
    ax2.legend([f"Zone {z}" for z in "ABC"], loc="lower left", frameon=False, ncol=3)
    fig.savefig(OUT / "telemetry-preview.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_cleaning(reco) -> None:
    last = reco["date_day"].max()
    d = reco[(reco["date_day"] == last) & (reco["recommendation"] != "OK")].sort_values("lost_revenue_mad")
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(d) + 1.6))
    colors = [STATUS[r] for r in d["recommendation"]]
    ax.barh(d["array_id"], d["lost_revenue_mad"], color=colors, height=0.62, edgecolor=SURFACE, linewidth=2)
    for y, (v, r, loss) in enumerate(zip(d["lost_revenue_mad"], d["recommendation"], d["soiling_loss_pct"], strict=True)):
        ax.text(v + 4, y, f"{v:,.0f} MAD · {r} · -{loss:.1f} %", va="center", color=INK, fontsize=9)
    ax.set_xlim(0, d["lost_revenue_mad"].max() * 1.6)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Revenu perdu sur la journée (MAD)")
    ax.set_title(f"Plan de nettoyage du {pd.Timestamp(last):%d/%m/%Y} : où la poussière coûte le plus")
    handles = [matplotlib.patches.Patch(color=c, label=k) for k, c in STATUS.items() if k in set(d["recommendation"])]
    ax.legend(handles=handles, loc="lower right", frameon=False)
    fig.savefig(OUT / "cleaning-plan.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_canary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = demo.run("lost-dedup", Path(tmp), days=7)
    checks = report.checks
    badge = {"PASS": "#0ca30c", "WARN": "#fab219", "FAIL": "#d03b3b"}
    fig, ax = plt.subplots(figsize=(11, 0.36 * len(checks) + 1.7))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(len(checks) + 0.4, -1.9)
    ax.text(0, -1.4, f"Data Canary : {report.verdict}  ·  score {report.score} / 100",
            fontsize=15, weight="bold", color="#d03b3b" if report.verdict == "BLOCK" else "#0ca30c")
    ax.text(0, -0.75, "Scénario : un refactor du job Silver oublie la déduplication. Les tests unitaires passent, "
            "le nombre de lignes est identique...", fontsize=9.5, color=INK2)
    previous = None
    for i, c in enumerate(checks):
        if c.table != previous:
            ax.text(0, i, c.table, va="center", color=INK2, fontsize=9, family="DejaVu Sans Mono")
            previous = c.table
        ax.text(0.30, i, c.check, va="center", color=INK, fontsize=9)
        ax.text(0.60, i, f" {c.status} ", ha="center", va="center", fontsize=8, weight="bold",
                color="white" if c.status != "WARN" else INK,
                bbox={"boxstyle": "round,pad=0.25", "fc": badge[c.status], "ec": "none"})
        ax.text(0.65, i, c.observed, va="center", color=INK, fontsize=9)
        ax.axhline(i + 0.5, color=GRID, lw=0.6)
    fig.savefig(OUT / "canary-report.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    stats, silver, daily, reco = build()
    print("weather:", stats.weather)
    chart_telemetry(stats, silver, daily)
    chart_cleaning(reco)
    chart_canary()
    print("written:", *sorted(p.name for p in OUT.glob("*.png")))
