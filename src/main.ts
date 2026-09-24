import './style.css';

interface PeriodStats {
  commits: number;
  linesAdded: number;
  linesDeleted: number;
  netLines: number;
}

interface YearStats extends PeriodStats {
  year: string;
}

interface StatsPayload {
  schemaVersion: number;
  generatedAt: string | null;
  source: string;
  totals: {
    repositories: number;
    commits: number;
    linesAdded: number;
    linesDeleted: number;
    netLines: number;
    activeDays: number;
    firstCommitAt: string | null;
    lastCommitAt: string | null;
  };
  byYear: YearStats[];
  byMonth: Array<PeriodStats & { month: string }>;
  repositories: Array<Record<string, unknown>>;
  meta: {
    repositoryNamesPublished: boolean;
    scanErrors?: number;
  };
}

type LineTone = 'muted' | 'success' | 'warning' | 'normal';

interface OutputLine {
  label?: string;
  content: string;
  tone?: LineTone;
}

const output = document.querySelector<HTMLDivElement>('#output');
const finalPrompt = document.querySelector<HTMLDivElement>('#final-prompt');

const number = new Intl.NumberFormat('en-US');

const escapeHtml = (value: string): string =>
  value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

const pause = (milliseconds: number): Promise<void> =>
  new Promise(resolve => window.setTimeout(resolve, milliseconds));

const formatDate = (value: string | null): string => {
  if (!value) return '—';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';

  return parsed.toISOString().slice(0, 10);
};

const loadStats = async (): Promise<StatsPayload> => {
  const endpoints = ['/api/stats', '/data/stats.json'];

  let lastError: unknown;

  for (const endpoint of endpoints) {
    try {
      const response = await fetch(endpoint, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error(`${endpoint}: HTTP ${response.status}`);
      }
      return await response.json() as StatsPayload;
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError instanceof Error
    ? lastError
    : new Error('Unable to load statistics');
};

const buildLines = (stats: StatsPayload): OutputLine[] => {
  if (!stats.generatedAt) {
    return [
      { label: '[scan]', content: 'loading local-git snapshot...', tone: 'muted' },
      { label: '[warn]', content: 'no statistics have been published yet', tone: 'warning' },
      { content: 'run dev-stats run on the local machine and publish stats.json', tone: 'muted' }
    ];
  }

  const totals = stats.totals;
  const lines: OutputLine[] = [
    { label: '[scan]', content: 'source: local git history', tone: 'muted' },
    { label: '[scan]', content: `snapshot: ${formatDate(stats.generatedAt)}`, tone: 'muted' },
    { label: '[ok]', content: `repositories  ${number.format(totals.repositories)}`, tone: 'success' },
    { label: '[ok]', content: `commits       ${number.format(totals.commits)}`, tone: 'success' },
    {
      label: '[ok]',
      content: `lines         +${number.format(totals.linesAdded)}  -${number.format(totals.linesDeleted)}`,
      tone: 'success'
    },
    { label: '[ok]', content: `net lines     ${number.format(totals.netLines)}`, tone: 'success' },
    { label: '[ok]', content: `active days   ${number.format(totals.activeDays)}`, tone: 'success' },
    { label: '[ok]', content: `first commit  ${formatDate(totals.firstCommitAt)}`, tone: 'success' },
    { label: '[ok]', content: `last commit   ${formatDate(totals.lastCommitAt)}`, tone: 'success' }
  ];

  const recentYears = stats.byYear.slice(-6);
  if (recentYears.length > 0) {
    lines.push({ content: '', tone: 'normal' });
    lines.push({ label: '[year]', content: 'commits      lines', tone: 'muted' });

    for (const year of recentYears) {
      lines.push({
        content:
          `${year.year}    ${String(number.format(year.commits)).padStart(8)}    ` +
          `+${number.format(year.linesAdded)} / -${number.format(year.linesDeleted)}`,
        tone: 'normal'
      });
    }
  }

  if ((stats.meta.scanErrors ?? 0) > 0) {
    lines.push({
      label: '[warn]',
      content: `skipped repositories: ${stats.meta.scanErrors}`,
      tone: 'warning'
    });
  }

  lines.push({ content: '', tone: 'normal' });
  lines.push({ label: '[done]', content: 'dev-stats completed', tone: 'success' });

  return lines;
};

const appendLine = (line: OutputLine): void => {
  if (!output) return;

  const element = document.createElement('div');
  element.className = `terminal-line tone-${line.tone ?? 'normal'}`;

  const label = line.label
    ? `<span class="output-label">${escapeHtml(line.label)}</span> `
    : '';

  element.innerHTML = `${label}<span>${escapeHtml(line.content)}</span>`;
  output.append(element);
};

const reveal = async (lines: OutputLine[]): Promise<void> => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  for (const line of lines) {
    appendLine(line);

    if (!reducedMotion) {
      const delay = line.content === '' ? 80 : 115;
      await pause(delay);
    }
  }

  if (finalPrompt) {
    finalPrompt.hidden = false;
  }
};

const main = async (): Promise<void> => {
  try {
    const stats = await loadStats();
    await reveal(buildLines(stats));
  } catch (error) {
    await reveal([
      { label: '[error]', content: 'failed to load statistics', tone: 'warning' },
      {
        content: error instanceof Error ? error.message : 'unknown error',
        tone: 'muted'
      }
    ]);
  }
};

void main();
