import { describe, it, expect } from 'vitest'
import { validateXiaoyuzhouUrl } from './analyzeHelpers'

describe('validateXiaoyuzhouUrl', () => {
  it('accepts a well-formed episode link with 24-hex id', () => {
    const url = 'https://www.xiaoyuzhoufm.com/episode/67e8a1b2c3d4e5f60718293a'
    expect(validateXiaoyuzhouUrl(url)).toBeNull()
  })

  it('rejects a podcast page with the notEpisode message', () => {
    const url = 'https://www.xiaoyuzhoufm.com/podcast/abc123'
    expect(validateXiaoyuzhouUrl(url)).toBe('analyze.xiaoyuzhouNotEpisode')
  })

  it('rejects a user page with the notEpisode message', () => {
    const url = 'https://www.xiaoyuzhoufm.com/user/abc123'
    expect(validateXiaoyuzhouUrl(url)).toBe('analyze.xiaoyuzhouNotEpisode')
  })

  it('rejects an episode link whose id is not 24 hex chars (badFormat)', () => {
    const url = 'https://www.xiaoyuzhoufm.com/episode/short'
    expect(validateXiaoyuzhouUrl(url)).toBe('analyze.xiaoyuzhouBadFormat')
  })

  it('rejects a non-xiaoyuzhou link with the generic invalid message', () => {
    const url = 'https://www.youtube.com/watch?v=abc'
    expect(validateXiaoyuzhouUrl(url)).toBe('analyze.invalidXiaoyuzhou')
  })
})
