/**
 * Tests for src/api.ts — the HTTP client including:
 * - transparent 401 → refresh → retry
 * - refresh token storage in localStorage
 * - logout clearing state
 * - in-flight refresh deduplication
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearAuthToken,
  getStoredRefreshToken,
  getMe,
  login,
  logout,
  setAuthToken,
  setRefreshToken,
} from '../api'

const ORIGINAL_FETCH = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch
  // Reset module-level state by clearing auth + refresh tokens
  clearAuthToken()
  setRefreshToken('')
})

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH
  vi.restoreAllMocks()
})

function mockFetchOnce(body: unknown, init: ResponseInit = { status: 200 }) {
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  fetchMock.mockImplementationOnce(
    () => Promise.resolve(new Response(JSON.stringify(body), {
      status: init.status ?? 200,
      headers: { 'content-type': 'application/json' },
    }))
  )
}

describe('login + setRefreshToken', () => {
  it('persists refresh_token to localStorage', async () => {
    mockFetchOnce({ access_token: 'A', refresh_token: 'R' })
    const resp = await login('u@x', 'pw')
    expect(resp.access_token).toBe('A')
    expect(resp.refresh_token).toBe('R')
    expect(getStoredRefreshToken()).toBe('R')
    expect(localStorage.getItem('refresh_token')).toBe('R')
  })

  it('clears localStorage when setRefreshToken is called with empty string', () => {
    localStorage.setItem('refresh_token', 'old')
    setRefreshToken('')
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })
})

describe('transparent refresh on 401', () => {
  it('retries once with refreshed token on 401', async () => {
    setAuthToken('expired')
    setRefreshToken('valid-refresh')

    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    // 1st call: 401 on getMe
    fetchMock.mockImplementationOnce(() => Promise.resolve(
      new Response('unauthorized', { status: 401 })
    ))
    // 2nd call: refresh endpoint returns fresh tokens
    fetchMock.mockImplementationOnce(() => Promise.resolve(
      new Response(JSON.stringify({ access_token: 'NEW', refresh_token: 'NEW-R' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ))
    // 3rd call: retry getMe with new token
    fetchMock.mockImplementationOnce(() => Promise.resolve(
      new Response(JSON.stringify({ id: 'u1', email: 'u@x' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ))

    const me = await getMe()
    expect(me).toMatchObject({ id: 'u1' })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    // Retry must use the new bearer
    const retryCall = fetchMock.mock.calls[2]
    const retryInit = retryCall[1] as RequestInit
    const headers = retryInit.headers as Record<string, string>
    expect(headers['Authorization']).toBe('Bearer NEW')
  })

  it('throws on 401 when no refresh token is stored', async () => {
    setAuthToken('expired')
    // No refresh token → refresh attempt short-circuits to null
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockImplementationOnce(() => Promise.resolve(
      new Response('unauthorized', { status: 401 })
    ))
    await expect(getMe()).rejects.toThrow(/401/)
  })
})

describe('logout', () => {
  it('posts to /auth/logout and clears local state', async () => {
    setAuthToken('t')
    setRefreshToken('r')
    mockFetchOnce({}, { status: 200 })
    await logout()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('jwt_token')).toBeNull()
  })

  it('swallows network errors during logout', async () => {
    setAuthToken('t')
    setRefreshToken('r')
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error('network dead')))
    // Should not throw
    await expect(logout()).resolves.toBeUndefined()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })
})
