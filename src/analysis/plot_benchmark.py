"""Create a report figure from the clean-import benchmark (requires matplotlib)."""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', default='results/benchmark_clean_imports/summary.csv')
    ap.add_argument('--out', default='reports/figures')
    a = ap.parse_args()
    with open(a.input, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    algorithms = ['CHARM', 'FP-Growth', 'Apriori']
    supports = sorted({r['min_support'] for r in rows}, key=float, reverse=True)
    lookup = {(r['algorithm'], r['min_support']): r for r in rows}
    if len(lookup) != len(rows) or len(rows) != len(supports) * len(algorithms):
        raise ValueError('Expected exactly one row per algorithm and support')
    incomplete_rows = [r for r in rows if int(r['valid_runs']) != int(r['requested_runs'])]
    repeat_counts = sorted({int(r['valid_runs']) for r in rows})
    fig, ax = plt.subplots(figsize=(10, 6.8))
    colors = ['#167D8D', '#E69F00', '#6763AD']
    for j, algo in enumerate(algorithms):
        for i, support in enumerate(supports):
            row = lookup[algo, support]
            seconds = float(row['median_seconds'])
            valid, requested = int(row['valid_runs']), int(row['requested_runs'])
            if valid < 1 or seconds <= 0:
                raise ValueError('Cannot plot a configuration without valid positive timing')
            incomplete = valid < requested
            ax.bar(i + (j - 1) * 0.25, seconds, width=0.23,
                   color=colors[j], edgecolor='#222222', linewidth=0.5,
                   hatch='///' if incomplete else '',
                   label=algo if i == 0 else None, zorder=3)
            ax.annotate(f'{seconds:.2f}' + ('*' if incomplete else ''),
                        (i + (j - 1) * 0.25, seconds), xytext=(0, 5),
                        textcoords='offset points', ha='center', fontsize=9)
    ax.set_yscale('log')
    ax.set_ylim(0.1, 1000)
    ax.set_yticks([0.1, 1, 10, 100, 1000], labels=['0,1', '1', '10', '100', '1.000'])
    ax.set_xticks(range(len(supports)), [f'{float(s) * 100:g}%' for s in supports])
    ax.set_xlabel('Ngưỡng support', labelpad=10)
    ax.set_ylabel('Thời gian trung vị (giây) — thang logarit')
    ax.set_title('Thời gian khai thác trên Online Retail II', fontsize=15, pad=16)
    ax.grid(axis='y', which='major', alpha=0.25, zorder=0)
    ax.spines[['top', 'right']].set_visible(False)
    ax.legend(loc='upper left', frameon=False)
    fig.subplots_adjust(bottom=0.25, top=0.88, left=0.11, right=0.97)
    repetitions = f'{repeat_counts[0]} lần hợp lệ mỗi cấu hình.' if len(repeat_counts) == 1 else 'Số lần hợp lệ khác nhau giữa các cấu hình.'
    fig.text(0.11, 0.13, f'Trung vị thời gian • {repetitions}', fontsize=10)
    note = '* Cột gạch chéo: chưa đủ số lần hợp lệ yêu cầu.' if incomplete_rows else 'Cả 9 cấu hình trong phiên benchmark hiện tại đều đủ 3 lần hợp lệ.'
    if not incomplete_rows and (len(rows) != 9 or repeat_counts != [3]):
        note = 'Tất cả cấu hình trong bảng đều đủ số lần hợp lệ yêu cầu.'
    fig.text(0.11, 0.09, note, fontsize=9)
    fig.text(0.11, 0.05, f'Nguồn: {Path(a.input).as_posix()}', fontsize=9)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for suffix in ['png', 'svg']:
        path = out / f'benchmark_time.{suffix}'
        fig.savefig(path, dpi=300, facecolor='white')
        print(f'Saved: {path}')
    plt.close(fig)


if __name__ == '__main__':
    main()
