import { describe, it, expect } from 'vitest'

describe('api base url', () => {
  it('exposes a non-empty API base', async () => {
    const { getApiBase } = await import('../src/services/api')
    expect(typeof getApiBase()).toBe('string')
    expect(getApiBase().length).toBeGreaterThan(0)
  })
})
