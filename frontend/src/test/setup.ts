import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Node 22+ ships an experimental built-in Web Storage that shadows the
// jsdom one. Force an in-memory shim so behaviour is predictable and
// .clear() / .removeItem() work consistently across tests.
class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length() { return this.store.size }
  clear() { this.store.clear() }
  getItem(k: string) { return this.store.has(k) ? this.store.get(k)! : null }
  key(i: number) { return Array.from(this.store.keys())[i] ?? null }
  removeItem(k: string) { this.store.delete(k) }
  setItem(k: string, v: string) { this.store.set(k, String(v)) }
}

const memStore = new MemoryStorage()
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: memStore })
Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: new MemoryStorage() })

beforeEach(() => {
  memStore.clear()
})

afterEach(() => {
  cleanup()
  memStore.clear()
})
