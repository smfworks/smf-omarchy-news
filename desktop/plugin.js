/**
 * SMF Omarchy News — official Omarchy Linux news in a right-of-chat pane.
 * Disk plugin: jsx/jsxs only. Never invent headlines.
 */
import {
  Badge,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  ScrollArea,
  SegmentedControl,
  Separator,
  cn,
  fmtDateTime,
  haptic,
  host,
  PALETTE_AREA,
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  atom,
  relativeTime,
  useQuery,
  useQueryClient,
  useValue,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const ID = 'smf-omarchy-news'
const ROUTE = '/omarchy-news'
const POLL_MS = 5 * 60 * 1000
const $view = atom({ mode: 'feed', articleId: null, feedScroll: 0 })
const $filter = atom('all')

function emptyFeed() {
  return {
    ok: true,
    stale: false,
    read_status: 'ok',
    empty: true,
    items: [],
    count: 0,
    sources: [],
    errors: [],
    warnings: [],
    fetched_at: null,
    cache_age_seconds: null,
  }
}

function asItems(data) {
  if (!data || !Array.isArray(data.items)) return []
  return data.items.filter((it) => it && typeof it === 'object' && it.title)
}

function hasReadProblems(data) {
  if (!data) return false
  if (data.ok === false) return true
  if (data.read_status === 'unread') return true
  const errs = data.errors
  return Array.isArray(errs) && errs.length > 0 && asItems(data).length === 0
}

function isUnread(data) {
  if (!data) return false
  return (data.ok === false || data.read_status === 'unread') && asItems(data).length === 0
}

function formatErrors(data) {
  const errs = (data && data.errors) || []
  const lines = errs
    .map((e) => {
      if (!e) return ''
      if (e.path) return `${e.path}: ${e.error || e.kind || 'failed'}`
      if (e.kind && e.error) return `${e.kind}: ${e.error}`
      return e.error || e.kind || ''
    })
    .filter(Boolean)
  if (data && data.error && !lines.length) lines.push(String(data.error))
  if (!lines.length) {
    return 'Could not load Omarchy news. This is not an empty day — the feed was unread. No headlines were invented.'
  }
  return lines.slice(0, 4).join(' · ')
}

function fmtAge(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return null
  const s = Math.max(0, Math.trunc(Number(seconds)))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

function fmtWhen(value) {
  if (!value) return ''
  try {
    const rel = relativeTime(value)
    if (rel) return rel
  } catch {
    /* formatter optional */
  }
  try {
    return fmtDateTime(value)
  } catch {
    return String(value)
  }
}

function sourceKind(item) {
  const name = String((item && item.source_name) || '')
  if (name === 'Omarchy.org') return 'official'
  if (name === 'Omarchy releases') return 'releases'
  return 'community'
}

function filterItems(items, filter) {
  if (filter === 'official') return items.filter((it) => sourceKind(it) === 'official')
  if (filter === 'releases') return items.filter((it) => sourceKind(it) === 'releases')
  if (filter === 'community') return items.filter((it) => sourceKind(it) === 'community')
  return items
}

function openExternal(url) {
  if (!url) return
  const tryRequest = (method, params) => {
    try {
      const p = host.request(method, params)
      if (p && typeof p.then === 'function') return p
      return Promise.resolve(p)
    } catch (err) {
      return Promise.reject(err)
    }
  }
  const fallback = () => {
    try {
      if (typeof window !== 'undefined' && typeof window.open === 'function') {
        window.open(url, '_blank', 'noopener,noreferrer')
      }
    } catch {
      /* host blocked popups */
    }
  }
  try {
    if (typeof host.openExternal === 'function') {
      void Promise.resolve(host.openExternal(url)).catch(fallback)
      return
    }
    if (typeof host.open === 'function') {
      void Promise.resolve(host.open(url)).catch(fallback)
      return
    }
  } catch {
    /* continue */
  }
  void tryRequest('os.openExternal', { url })
    .catch(() => tryRequest('os.open', { url }))
    .catch(() => tryRequest('shell.openExternal', { url }))
    .catch(fallback)
}

async function fetchFeed(ctx, refresh) {
  const path = refresh ? '/feed?refresh=1' : '/feed'
  const data = await ctx.rest(path)
  if (!data || typeof data !== 'object') {
    throw new Error('Feed response was empty')
  }
  return data
}

async function fetchArticle(ctx, id) {
  const data = await ctx.rest('/article?id=' + encodeURIComponent(id))
  if (!data || typeof data !== 'object') {
    throw new Error('Article response was empty')
  }
  return data
}

function PlaceholderImage() {
  return jsxs('div', {
    className: cn(
      'flex h-28 w-full items-center justify-center rounded-md',
      'border border-dashed border-(--ui-stroke-secondary)',
      'text-(--ui-text-quaternary)'
    ),
    children: [
      jsx(Codicon, { name: 'file-media', size: 18 }),
      jsx('span', { className: 'sr-only', children: 'No image' }),
    ],
  })
}

function CardImage({ src, alt }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken) {
    return src ? jsx(PlaceholderImage, {}) : null
  }
  return jsx('img', {
    src,
    alt: alt || '',
    className: 'h-28 w-full rounded-md object-cover',
    onError: () => setBroken(true),
  })
}

function HeroImage({ src, alt }) {
  const [broken, setBroken] = useState(false)
  if (!src || broken) {
    return src ? jsx(PlaceholderImage, {}) : null
  }
  return jsx('img', {
    src,
    alt: alt || '',
    className: 'max-h-48 w-full rounded-md object-cover',
    onError: () => setBroken(true),
  })
}

function NewsCard({ item, onOpen }) {
  return jsx('button', {
    type: 'button',
    onClick: () => {
      haptic('tap')
      onOpen(item)
    },
    className: cn(
      'flex w-full flex-col gap-2 rounded-xl border border-(--ui-stroke-secondary)',
      'bg-transparent px-3 py-3 text-left transition-colors',
      'hover:border-(--ui-accent)'
    ),
    children: jsxs('div', {
      className: 'flex flex-col gap-2',
      children: [
        item.image_url ? jsx(CardImage, { src: item.image_url, alt: item.title }) : null,
        jsx('div', {
          className: 'text-sm font-medium leading-snug',
          children: item.title,
        }),
        item.lede
          ? jsx('div', {
              className: 'line-clamp-4 text-[0.8125rem] leading-relaxed text-(--ui-text-secondary)',
              children: item.lede,
            })
          : null,
        jsxs('div', {
          className: 'flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.6875rem] text-(--ui-text-tertiary)',
          children: [
            jsx(Badge, {
              className: 'shrink-0 text-[0.625rem]',
              children: item.source_name || 'source',
            }),
            item.published_at
              ? jsx('span', { children: fmtWhen(item.published_at) })
              : jsx('span', { children: 'undated' }),
          ],
        }),
      ],
    }),
  })
}

function ArticleBody({ item }) {
  const htmlBody = item && item.body_html
  const text = item && item.body_text
  if (htmlBody) {
    return jsx('div', {
      className: cn(
        'text-[0.8125rem] leading-relaxed text-(--ui-text-secondary)',
        '[&_a]:text-(--ui-accent) [&_a]:underline',
        '[&_p]:mb-3 [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-sm [&_h2]:font-medium',
        '[&_h3]:mb-2 [&_h3]:mt-3 [&_h3]:text-sm [&_h3]:font-medium',
        '[&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-4',
        '[&_code]:rounded [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.75rem] [&_code]:text-(--ui-text-secondary)',
        '[&_pre]:mb-3 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:border-(--ui-stroke-secondary) [&_pre]:p-2',
        '[&_img]:my-3 [&_img]:max-h-56 [&_img]:w-full [&_img]:rounded-md [&_img]:object-cover',
        '[&_blockquote]:border-l-2 [&_blockquote]:border-(--ui-stroke-secondary) [&_blockquote]:pl-3'
      ),
      dangerouslySetInnerHTML: { __html: htmlBody },
    })
  }
  if (text) {
    return jsx('div', {
      className: 'whitespace-pre-wrap text-[0.8125rem] leading-relaxed text-(--ui-text-secondary)',
      children: text,
    })
  }
  if (item && item.lede) {
    return jsxs('div', {
      className: 'flex flex-col gap-2',
      children: [
        jsx('div', {
          className: 'text-[0.8125rem] leading-relaxed text-(--ui-text-secondary)',
          children: item.lede,
        }),
        jsx('div', {
          className: 'text-xs text-(--ui-text-tertiary)',
          children: 'Full text was not available from the source. Nothing extra was written here.',
        }),
      ],
    })
  }
  return jsx(EmptyState, {
    title: 'No body on this item',
    description: 'The source did not include article text. Open the original instead of guessing.',
  })
}

function restoreFeedScroll(root, top) {
  if (!root || typeof top !== 'number') return
  const vp =
    root.querySelector('[data-radix-scroll-area-viewport]') ||
    root.querySelector('[data-scroll-viewport]') ||
    root
  try {
    vp.scrollTop = top
  } catch {
    /* ignore */
  }
}

function captureFeedScroll(root) {
  if (!root) return 0
  const vp =
    root.querySelector('[data-radix-scroll-area-viewport]') ||
    root.querySelector('[data-scroll-viewport]') ||
    root
  return vp.scrollTop || 0
}

function ReaderView({ ctx, articleId, listingItem, onBack }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'article', articleId],
    queryFn: () => fetchArticle(ctx, articleId),
    enabled: Boolean(articleId),
    staleTime: 60 * 1000,
    retry: 1,
  })
  const item = (data && data.item) || listingItem
  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 px-3 py-2',
        children: [
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              onBack()
            },
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1',
              children: [jsx(Codicon, { name: 'chevron-left', size: 14 }), 'Back'],
            }),
          }),
          jsx('div', { className: 'min-w-0 flex-1 truncate text-xs text-(--ui-text-tertiary)', children: 'Article' }),
        ],
      }),
      jsx(Separator, {}),
      isLoading && !item
        ? jsxs('div', {
            className: 'flex flex-1 flex-col items-center justify-center gap-3',
            children: [
              jsx(GlyphSpinner, { size: 22 }),
              jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'Loading article…' }),
            ],
          })
        : jsx(ScrollArea, {
            className: 'min-h-0 flex-1',
            children: jsxs('div', {
              className: 'flex flex-col gap-3 px-4 py-3 pb-8',
              children: [
                jsx('div', {
                  className: 'text-base font-medium leading-snug',
                  children: (item && item.title) || 'Untitled',
                }),
                jsxs('div', {
                  className: 'flex flex-wrap items-center gap-2 text-[0.6875rem] text-(--ui-text-tertiary)',
                  children: [
                    item && item.source_name
                      ? jsx(Badge, { className: 'text-[0.625rem]', children: item.source_name })
                      : null,
                    item && item.published_at
                      ? jsx('span', { children: fmtDateTime ? fmtWhen(item.published_at) : item.published_at })
                      : null,
                  ],
                }),
                item && item.image_url ? jsx(HeroImage, { src: item.image_url, alt: item.title }) : null,
                data && data.errors && data.errors.length
                  ? jsx('div', {
                      className:
                        'rounded-md border border-(--ui-stroke-secondary) px-3 py-2 text-xs text-(--ui-text-secondary)',
                      children: formatErrors(data),
                    })
                  : null,
                error && !item
                  ? jsx(ErrorState, {
                      title: 'Could not load article',
                      description: error.message || 'The backend did not return this item. Nothing was invented.',
                    })
                  : jsx(ArticleBody, { item: item || {} }),
                jsxs('div', {
                  className: 'flex flex-wrap items-center gap-2 pt-2',
                  children: [
                    item && item.source_url
                      ? jsx(Button, {
                          variant: 'ghost',
                          size: 'sm',
                          onClick: () => {
                            haptic('tap')
                            openExternal(item.source_url)
                          },
                          children: jsxs('span', {
                            className: 'inline-flex items-center gap-1.5',
                            children: [jsx(Codicon, { name: 'link-external', size: 14 }), 'Open original'],
                          }),
                        })
                      : null,
                    error || (data && data.errors && data.errors.length)
                      ? jsx(Button, {
                          variant: 'ghost',
                          size: 'sm',
                          onClick: () => refetch(),
                          children: 'Retry',
                        })
                      : null,
                  ],
                }),
              ],
            }),
          }),
    ],
  })
}

function FeedHeader({ data, isFetching, onRefresh }) {
  const stale = Boolean(data && data.stale)
  const age = data && fmtAge(data.cache_age_seconds)
  return jsxs('div', {
    className: 'flex flex-col gap-2 px-4 pt-4',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Codicon, { name: 'rss', size: 16 }),
          jsx('div', { className: 'min-w-0 flex-1 truncate text-sm font-medium', children: 'Omarchy News' }),
          stale
            ? jsx(Badge, {
                className: 'text-[0.625rem]',
                children: age ? `stale · ${age}` : 'stale',
              })
            : null,
          isFetching
            ? jsx('span', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: 'updating' })
            : null,
          jsx(Button, {
            variant: 'ghost',
            size: 'sm',
            onClick: () => {
              haptic('tap')
              onRefresh()
            },
            children: 'Refresh',
          }),
        ],
      }),
      jsx('div', {
        className: 'text-[0.6875rem] leading-relaxed text-(--ui-text-tertiary)',
        children:
          'Official Omarchy.org posts and GitHub releases, plus labeled community links. Not the whole Omarchy universe.',
      }),
    ],
  })
}

function NewsPane({ ctx, markSeen }) {
  const view = useValue($view)
  const filter = useValue($filter)
  const feedRef = useRef(null)
  const queryClient = useQueryClient()
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: [ID, 'feed'],
    queryFn: () => fetchFeed(ctx, false),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
    retry: 1,
  })
  const items = asItems(data)
  const visible = filterItems(items, filter)
  const unread = Boolean(error && !data) || isUnread(data)
  const listingItem = items.find((it) => it.id === view.articleId) || null

  useEffect(() => {
    if (view.mode === 'feed' && feedRef.current) {
      restoreFeedScroll(feedRef.current, view.feedScroll)
    }
  }, [view.mode, view.feedScroll])

  useEffect(() => {
    if (markSeen && data && data.ok !== false && items.length) {
      try {
        const newest = items[0] && items[0].published_at
        const prev = ctx.storage.get('lastOpenedAt')
        ctx.storage.set('lastOpenedAt', new Date().toISOString())
        if (!prev && newest) ctx.storage.set('baselinePublishedAt', newest)
      } catch {
        /* storage optional */
      }
    }
  }, [markSeen, data, items.length])

  const openItem = (item) => {
    const scroll = captureFeedScroll(feedRef.current)
    $view.set({ mode: 'reader', articleId: item.id, feedScroll: scroll })
  }

  const goBack = () => {
    $view.set({ mode: 'feed', articleId: null, feedScroll: view.feedScroll })
  }

  if (view.mode === 'reader' && view.articleId) {
    return jsx(ReaderView, { ctx, articleId: view.articleId, listingItem, onBack: goBack })
  }

  if (isLoading) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3',
      children: [
        jsx(GlyphSpinner, { size: 24 }),
        jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'Loading Omarchy news…' }),
      ],
    })
  }

  if (unread || (hasReadProblems(data) && items.length === 0)) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-center justify-center gap-3 p-8',
      children: [
        jsx(ErrorState, {
          title: error && !data ? 'Backend not reachable' : 'Could not load Omarchy news',
          description:
            error && !data
              ? 'Enable Omarchy News in Settings → Plugins, then quit Hermes Desktop and relaunch from the menu. Reload desktop plugins is JS only. No headlines were invented.'
              : formatErrors(data),
        }),
        jsx(Button, { variant: 'ghost', size: 'sm', onClick: () => refetch(), children: 'Retry' }),
      ],
    })
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col gap-3',
    children: [
      jsx(FeedHeader, {
        data,
        isFetching,
        onRefresh: () => {
          fetchFeed(ctx, true)
            .then((fresh) => {
              queryClient.setQueryData([ID, 'feed'], fresh)
            })
            .catch(() => {
              void refetch()
            })
        },
      }),
      data && data.stale
        ? jsx('div', {
            className: 'px-4 text-xs text-(--ui-text-secondary)',
            children:
              'Showing cached items because a refresh failed. Age ' +
              (fmtAge(data.cache_age_seconds) || 'unknown') +
              '. These are not new headlines.',
          })
        : null,
      data && Array.isArray(data.errors) && data.errors.length && items.length
        ? jsx('div', {
            className:
              'mx-4 rounded-md border border-(--ui-stroke-secondary) px-3 py-2 text-xs text-(--ui-text-secondary)',
            children: formatErrors(data),
          })
        : null,
      jsx('div', {
        className: 'px-4',
        children: jsx(SegmentedControl, {
          value: filter,
          onChange: (v) => $filter.set(v),
          options: [
            { id: 'all', label: 'All' },
            { id: 'official', label: 'Official' },
            { id: 'releases', label: 'Releases' },
            { id: 'community', label: 'Community' },
          ],
        }),
      }),
      jsx(Separator, {}),
      visible.length === 0
        ? jsx('div', {
            className: 'flex flex-1 items-center justify-center p-6',
            children: jsx(EmptyState, {
              title: items.length === 0 ? 'No news in cache' : 'Nothing in this filter',
              description:
                items.length === 0
                  ? 'Sources returned zero items. That is empty, not an error — and not an invented briefing.'
                  : 'No posts from this slice of the feed.',
            }),
          })
        : jsx('div', {
            ref: feedRef,
            className: 'min-h-0 flex-1',
            children: jsx(ScrollArea, {
              className: 'h-full',
              children: jsx('div', {
                className: 'flex flex-col gap-2 px-4 pb-6',
                children: visible.map((item) =>
                  jsx(NewsCard, { item, onOpen: openItem }, item.id || item.source_url)
                ),
              }),
            }),
          }),
    ],
  })
}

function newCountFrom(data, ctx) {
  const items = asItems(data)
  if (!items.length) return 0
  let last = null
  try {
    last = ctx.storage.get('lastOpenedAt')
  } catch {
    last = null
  }
  if (!last) return 0
  const lastMs = Date.parse(String(last))
  if (!Number.isFinite(lastMs)) return 0
  let n = 0
  for (const it of items) {
    if (!it.published_at) continue
    const ms = Date.parse(String(it.published_at))
    if (Number.isFinite(ms) && ms > lastMs) n += 1
  }
  return n
}

function NewChipHost({ ctx }) {
  const { data } = useQuery({
    queryKey: [ID, 'feed'],
    queryFn: () => fetchFeed(ctx, false),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
    retry: 0,
  })
  if (!data || data.ok === false) return null
  const n = newCountFrom(data, ctx)
  if (!n) return null
  return jsx('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-secondary)',
    onClick: () => {
      haptic('tap')
      host.navigate(ROUTE)
    },
    children: jsxs('span', {
      className: 'inline-flex items-center gap-1',
      children: [jsx(Codicon, { name: 'rss', size: 12 }), `${n} new`],
    }),
  })
}

export default {
  id: ID,
  name: 'Omarchy News',
  defaultEnabled: true,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Omarchy News',
        data: {
          placement: 'right',
          width: '400px',
          dock: { pane: 'workspace', pos: 'right' },
        },
        render: () => jsx(NewsPane, { ctx, markSeen: true }),
      },
      {
        id: `${ID}-nav`,
        area: SIDEBAR_NAV_AREA,
        data: { path: ROUTE, label: 'Omarchy News', codicon: 'rss' },
      },
      {
        id: `${ID}-route`,
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(NewsPane, { ctx, markSeen: true }),
      },
      {
        id: `${ID}-palette`,
        area: PALETTE_AREA,
        data: {
          id: `${ID}-open`,
          label: 'Open Omarchy News',
          keywords: ['omarchy', 'news', 'linux', 'dhh', 'release'],
          run: () => host.navigate(ROUTE),
        },
      },
      {
        id: `${ID}-new-chip`,
        area: STATUSBAR_AREAS.right,
        order: 142,
        render: () => jsx(NewChipHost, { ctx }),
      },
    ])
  },
}
