import { useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { formatDuration, formatTimeRange, platformLabel } from '../lib/utils';

interface PublicSegment {
  segment_index: number;
  title: string;
  summary: string;
  start_seconds: number;
  end_seconds: number;
}

interface PublicNote {
  id: number;
  title: string;
  url: string;
  platform: string;
  duration_seconds: number;
  summary: string;
  essence: string;
  transcript: string;
  segments: PublicSegment[];
}

export default function ShareNotePage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const sig = params.get('sig') || '';
  const [note, setNote] = useState<PublicNote | null>(null);
  const [error, setError] = useState('');
  const invalidLink = !id || !sig;

  useEffect(() => {
    if (invalidLink) {
      return;
    }
    let cancelled = false;
    fetch(`/api/public/notes/${id}?sig=${encodeURIComponent(sig)}`)
      .then(async (resp) => {
        if (!resp.ok) throw new Error('not found');
        return resp.json() as Promise<PublicNote>;
      })
      .then((data) => {
        if (!cancelled) setNote(data);
      })
      .catch(() => {
        if (!cancelled) setError('找不到这篇笔记，或链接已失效。');
      });
    return () => {
      cancelled = true;
    };
  }, [id, sig, invalidLink]);

  if (invalidLink || error) {
    return (
      <main className="mx-auto max-w-2xl px-5 py-16 text-center text-[var(--color-text-secondary)]">
        {invalidLink ? '链接无效' : error}
      </main>
    );
  }
  if (!note) {
    return (
      <main className="mx-auto max-w-2xl px-5 py-16 text-center text-[var(--color-text-secondary)]">
        加载中…
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl px-5 py-10">
      <p className="mb-2 text-xs tracking-wide text-[var(--color-text-secondary)] uppercase">
        全文
      </p>
      <h1 className="text-2xl font-semibold leading-snug">{note.title || '未命名'}</h1>
      <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
        {platformLabel(note.platform)} · {formatDuration(note.duration_seconds)}
        {note.url ? (
          <>
            {' · '}
            <a
              href={note.url}
              className="text-[var(--color-primary)] underline"
              target="_blank"
              rel="noreferrer"
            >
              原片
            </a>
          </>
        ) : null}
      </p>

      {note.summary ? (
        <section className="mt-8">
          <h2 className="mb-2 text-lg font-medium">摘要</h2>
          <p className="whitespace-pre-wrap leading-7">{note.summary}</p>
        </section>
      ) : null}

      {note.essence ? (
        <section className="mt-8">
          <h2 className="mb-2 text-lg font-medium">精华</h2>
          <p className="whitespace-pre-wrap leading-7">{note.essence}</p>
        </section>
      ) : null}

      {note.segments.map((seg) => (
        <section
          key={seg.segment_index}
          className="mt-8 border-t border-[var(--color-border)] pt-6"
        >
          <h2 className="mb-1 text-lg font-medium">
            {seg.segment_index + 1}. {seg.title || '分段'}
          </h2>
          <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
            {formatTimeRange(seg.start_seconds, seg.end_seconds)}
          </p>
          {seg.summary ? <p className="whitespace-pre-wrap leading-7">{seg.summary}</p> : null}
        </section>
      ))}

      {note.transcript ? (
        <section className="mt-8 border-t border-[var(--color-border)] pt-6">
          <h2 className="mb-2 text-lg font-medium">逐字稿</h2>
          <p className="whitespace-pre-wrap font-mono text-sm leading-7 text-[var(--color-text-secondary)]">
            {note.transcript}
          </p>
        </section>
      ) : null}
    </main>
  );
}
