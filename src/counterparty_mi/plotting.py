"""Static Matplotlib charts (PNG). Uses the non-interactive Agg backend so no
graphical desktop is needed. Every figure is labelled as synthetic data and is
closed after saving."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from . import limits as L  # noqa: E402
from .schemas import RATINGS, UNRATED  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
NEUTRAL = "#9a9893"
STATUS_COLOURS = {
    L.WITHIN: "#0ca30c",
    L.WARNING: "#fab219",
    L.AT_LIMIT: "#ec835a",
    L.BREACH: "#d03b3b",
    L.ZERO_EXPOSURE: "#8f1f1f",
    L.ZERO_NO_EXPOSURE: "#c9c7c1",
    L.MISSING: NEUTRAL,
}
SYNTHETIC_NOTE = "SYNTHETIC DATA - fictional counterparties and valuations. Simplified current exposure only (no PFE / SA-CCR / CVA)."

FIGURE_FILES = {
    "exposure_trend": "exposure_trend.png",
    "top10_counterparties": "top10_counterparties.png",
    "limit_utilisation": "limit_utilisation.png",
    "exposure_by_rating": "exposure_by_rating.png",
    "exposure_by_sector": "exposure_by_sector.png",
    "collateral_comparison": "collateral_comparison.png",
    "limit_status_counts": "limit_status_counts.png",
}


def _gbp_m(x, _pos=None) -> str:
    return f"£{x / 1e6:,.1f}m"


def _setup(figsize=(10, 6)):
    plt.rcParams.update({
        "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": TEXT2, "xtick.color": TEXT2,
        "ytick.color": TEXT2, "axes.titlecolor": TEXT, "font.family": "DejaVu Sans",
    })
    fig, ax = plt.subplots(figsize=figsize, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _finish(fig, ax, title: str, subtitle: str, path: Path, footnote: str = "") -> Path:
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold", pad=26)
    ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction", xytext=(0, 8), textcoords="offset points",
                fontsize=9.5, color=TEXT2, va="bottom", ha="left")
    note = SYNTHETIC_NOTE + (f"\n{footnote}" if footnote else "")
    fig.text(0.01, 0.01, note, fontsize=8, color=TEXT2, va="bottom", ha="left", wrap=True)
    fig.tight_layout(rect=(0, 0.06 + 0.025 * footnote.count("\n") + (0.03 if footnote else 0), 1, 1))
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
    return path


def _no_data(ax, text: str) -> None:
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", fontsize=12, color=TEXT2)
    ax.set_xticks([])
    ax.set_yticks([])


def exposure_trend(daily: pd.DataFrame, path: Path) -> Path:
    fig, ax = _setup((11, 6))
    d = daily.sort_values("as_of_date")
    series = [("gross_positive_mtm_gbp", "Gross positive MTM"),
              ("pre_collateral_exposure_gbp", "Pre-collateral exposure (after netting)"),
              ("net_current_exposure_gbp", "Net current exposure (after netting and collateral)")]
    for (col, label), colour in zip(series, [SERIES[2], SERIES[1], SERIES[0]]):
        ax.plot(d["as_of_date"], d[col], color=colour, linewidth=2, label=label)
        ax.annotate(_gbp_m(d[col].iloc[-1]), (d["as_of_date"].iloc[-1], d[col].iloc[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=9, color=TEXT)
    ax.yaxis.set_major_formatter(FuncFormatter(_gbp_m))
    ax.set_ylabel("GBP (millions)")
    ax.set_xlabel("As-of date (business days)")
    ax.legend(frameon=False, loc="center left")
    ax.set_xlim(d["as_of_date"].iloc[0], d["as_of_date"].iloc[-1] + pd.Timedelta(days=6))
    fig.autofmt_xdate()
    span = f"{d['as_of_date'].iloc[0]:%Y-%m-%d} to {d['as_of_date'].iloc[-1]:%Y-%m-%d}"
    return _finish(fig, ax, "Daily counterparty exposure, all counterparties", f"{span} | GBP | sums across all netting sets", path)


def top10(top: pd.DataFrame, as_of: pd.Timestamp, path: Path) -> Path:
    fig, ax = _setup((10, 6))
    t = top[top["as_of_date"] == as_of].sort_values("rank", ascending=False)
    if t.empty:
        _no_data(ax, "No counterparty has positive net current exposure on this date")
    else:
        labels = [f"{r.counterparty_id}  {r.counterparty_name}" for r in t.itertuples()]
        ax.barh(labels, t["net_current_exposure_gbp"], color=SERIES[0], height=0.6)
        for y, r in enumerate(t.itertuples()):
            ax.text(r.net_current_exposure_gbp, y, f"  {_gbp_m(r.net_current_exposure_gbp)} ({r.share_of_total_net_exposure_pct:.1f}%)",
                    va="center", fontsize=9, color=TEXT)
        ax.xaxis.set_major_formatter(FuncFormatter(_gbp_m))
        ax.set_xlabel("Net current exposure (GBP millions)")
        ax.set_xlim(0, t["net_current_exposure_gbp"].max() * 1.3)
        ax.grid(axis="y", visible=False)
    return _finish(fig, ax, "Top 10 counterparties by net current exposure", f"As of {as_of:%Y-%m-%d} | GBP",
                   path, "Percentages: share of total net current exposure across all counterparties on the date.")


def limit_utilisation(cp: pd.DataFrame, as_of: pd.Timestamp, warning_pct: float, path: Path) -> Path:
    day = cp[cp["as_of_date"] == as_of]
    pos = day[day["limit_gbp"].notna() & (day["limit_gbp"] > 0)].sort_values("utilisation_pct")
    omitted = day[~day.index.isin(pos.index)]
    fig, ax = _setup((10, max(6, 0.24 * len(pos) + 2)))
    if pos.empty:
        _no_data(ax, "No counterparty has a positive effective limit on this date")
    else:
        colours = [STATUS_COLOURS[s] for s in pos["limit_status"]]
        ax.barh(pos["counterparty_id"], pos["utilisation_pct"], color=colours, height=0.65)
        ax.axvline(warning_pct, color=TEXT2, linestyle="--", linewidth=1.2)
        ax.axvline(100, color=TEXT, linestyle="-", linewidth=1.2)
        ax.text(warning_pct, 0.005, f" {warning_pct:.0f}% warning", transform=ax.get_xaxis_transform(), fontsize=8.5, color=TEXT2, va="bottom")
        ax.text(100, 0.005, " 100% limit", transform=ax.get_xaxis_transform(), fontsize=8.5, color=TEXT, va="bottom")
        for y, r in enumerate(pos.itertuples()):
            ax.text(r.utilisation_pct, y, f" {r.utilisation_pct:.1f}%", va="center", fontsize=8, color=TEXT)
        present = [s for s in (L.WITHIN, L.WARNING, L.AT_LIMIT, L.BREACH) if s in set(pos["limit_status"])]
        ax.legend(handles=[Patch(color=STATUS_COLOURS[s], label=s) for s in present], frameon=False, loc="center right")
        ax.set_xlabel("Utilisation (% of effective limit, GBP)")
        ax.set_xlim(0, max(110, pos["utilisation_pct"].max() * 1.12))
        ax.grid(axis="y", visible=False)
    if len(omitted):
        parts = [f"{r.counterparty_id} ({r.limit_status})" for r in omitted.sort_values("counterparty_id").itertuples()]
        foot = "Omitted (utilisation unavailable - missing or zero limit): " + ", ".join(parts)
    else:
        foot = "No counterparties omitted (all have positive effective limits)."
    return _finish(fig, ax, "Limit utilisation - counterparties with positive limits",
                   f"As of {as_of:%Y-%m-%d} | utilisation = net current exposure / limit (GBP)", path, foot)


def _bar_breakdown(frame: pd.DataFrame, dim: str, as_of: pd.Timestamp, order: list[str] | None, title: str,
                   path: Path, horizontal: bool) -> Path:
    day = frame[frame["as_of_date"] == as_of].set_index(dim)
    if order is not None:
        day = day.reindex([o for o in order if o in day.index])
    else:
        day = day.sort_values("net_current_exposure_gbp", ascending=True)
    fig, ax = _setup((10, 6))
    vals = day["net_current_exposure_gbp"]
    labels = [str(i) for i in day.index]
    if horizontal:
        ax.barh(labels, vals, color=SERIES[0], height=0.6)
        for y, (v, s) in enumerate(zip(vals, day["share_of_total_net_exposure_pct"])):
            ax.text(v, y, f"  {_gbp_m(v)}" + ("" if pd.isna(s) else f" ({s:.1f}%)"), va="center", fontsize=9, color=TEXT)
        ax.xaxis.set_major_formatter(FuncFormatter(_gbp_m))
        ax.set_xlabel("Net current exposure (GBP millions)")
        ax.set_xlim(0, max(vals.max(), 1) * 1.3)
        ax.grid(axis="y", visible=False)
    else:
        ax.bar(labels, vals, color=SERIES[0], width=0.6)
        for x, (v, s) in enumerate(zip(vals, day["share_of_total_net_exposure_pct"])):
            ax.text(x, v, _gbp_m(v) + ("" if pd.isna(s) else f"\n{s:.1f}%"), ha="center", va="bottom", fontsize=9, color=TEXT)
        ax.yaxis.set_major_formatter(FuncFormatter(_gbp_m))
        ax.set_ylabel("Net current exposure (GBP millions)")
        ax.set_ylim(0, max(vals.max(), 1) * 1.2)
        ax.grid(axis="x", visible=False)
    return _finish(fig, ax, title, f"As of {as_of:%Y-%m-%d} | GBP", path,
                   "Percentages: share of total net current exposure across all counterparties on the date.")


def exposure_by_rating(frame: pd.DataFrame, as_of: pd.Timestamp, path: Path) -> Path:
    return _bar_breakdown(frame, "rating", as_of, list(RATINGS) + [UNRATED],
                          "Net current exposure by counterparty rating (synthetic ratings)", path, horizontal=False)


def exposure_by_sector(frame: pd.DataFrame, as_of: pd.Timestamp, path: Path) -> Path:
    return _bar_breakdown(frame, "sector", as_of, None, "Net current exposure by sector", path, horizontal=True)


def collateral_comparison(cp: pd.DataFrame, as_of: pd.Timestamp, path: Path, n: int = 15) -> Path:
    day = cp[(cp["as_of_date"] == as_of) & (cp["pre_collateral_exposure_gbp"] > 0)]
    day = day.sort_values("pre_collateral_exposure_gbp", ascending=False).head(n).iloc[::-1]
    fig, ax = _setup((10, 7))
    if day.empty:
        _no_data(ax, "No counterparty has positive pre-collateral exposure on this date")
    else:
        y = np.arange(len(day))
        h = 0.38
        ax.barh(y + h / 2 + 0.01, day["pre_collateral_exposure_gbp"], height=h, color=SERIES[1], label="Pre-collateral exposure")
        ax.barh(y - h / 2 - 0.01, day["net_current_exposure_gbp"], height=h, color=SERIES[0], label="Net current exposure (after recognised collateral)")
        ax.set_yticks(y, day["counterparty_id"])
        ax.xaxis.set_major_formatter(FuncFormatter(_gbp_m))
        ax.set_xlabel("GBP (millions)")
        ax.legend(frameon=False, loc="lower right")
        ax.grid(axis="y", visible=False)
    return _finish(fig, ax, f"Pre-collateral vs net current exposure (top {n} by pre-collateral exposure)",
                   f"As of {as_of:%Y-%m-%d} | GBP", path,
                   "Collateral is recognised only if received, eligible and held against a netting-permitted set; surplus is not transferred between sets.")


def limit_status_counts(cp: pd.DataFrame, as_of: pd.Timestamp, path: Path) -> Path:
    day = cp[cp["as_of_date"] == as_of]
    order = [L.WITHIN, L.WARNING, L.AT_LIMIT, L.BREACH, L.ZERO_EXPOSURE, L.ZERO_NO_EXPOSURE, L.MISSING]
    counts = day["limit_status"].value_counts().reindex(order, fill_value=0)
    fig, ax = _setup((10, 6))
    ax.bar(range(len(order)), counts.values, color=[STATUS_COLOURS[s] for s in order], width=0.6)
    ax.set_xticks(range(len(order)), [s.replace(" — ", "\n") for s in order])
    for x, v in enumerate(counts.values):
        ax.text(x, v, str(int(v)), ha="center", va="bottom", fontsize=10, color=TEXT)
    ax.set_ylabel("Number of counterparties")
    ax.set_ylim(0, max(counts.max(), 1) * 1.15)
    ax.grid(axis="x", visible=False)
    return _finish(fig, ax, "Counterparties by business limit status", f"As of {as_of:%Y-%m-%d} | {len(day)} counterparties",
                   path, "Business limit status is separate from data-quality and reconciliation status.")


def make_all(cp: pd.DataFrame, daily: pd.DataFrame, top: pd.DataFrame, breakdowns: dict[str, pd.DataFrame],
             as_of: pd.Timestamp, warning_pct: float, fig_dir: Path) -> dict[str, Path]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    f = {k: fig_dir / v for k, v in FIGURE_FILES.items()}
    exposure_trend(daily, f["exposure_trend"])
    top10(top, as_of, f["top10_counterparties"])
    limit_utilisation(cp, as_of, warning_pct, f["limit_utilisation"])
    exposure_by_rating(breakdowns["rating"], as_of, f["exposure_by_rating"])
    exposure_by_sector(breakdowns["sector"], as_of, f["exposure_by_sector"])
    collateral_comparison(cp, as_of, f["collateral_comparison"])
    limit_status_counts(cp, as_of, f["limit_status_counts"])
    return f
