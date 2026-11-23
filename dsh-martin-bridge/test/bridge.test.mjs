import assert from 'node:assert/strict'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { apply, name, inject } from '../index.js'

function waitFor(check, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    const tick = () => {
      const value = check()
      if (value) return resolve(value)
      if (Date.now() - started > timeout) return reject(new Error('timeout'))
      setTimeout(tick, 20)
    }
    tick()
  })
}

test('bridge injects a wake into the live session and posts the reply', async (t) => {
  assert.equal(name, 'martin-bridge')
  assert.deepEqual(inject, ['agents', 'sessionQuery'])

  const posted = []
  const sseClients = new Set()
  const server = http.createServer((req, res) => {
    const pathname = req.url.replace(/^\/api\/v1/, '')
    if (pathname === '/wake') {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ schema: 1, wake: { kind: 'listener' } }))
      return
    }
    if (pathname === '/wake/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' })
      res.write(`id: 7\nevent: wake\ndata: ${JSON.stringify({
        schema: 1, kind: 'wake', groupId: 'g1', reason: 'mention', urgent: false,
        groupKind: 'temp', policy: 'GLOBAL_TEMP_POLICY: guest is not the owner',
      })}\n\n`)
      sseClients.add(res)
      req.on('close', () => sseClients.delete(res))
      return
    }
    if (pathname.startsWith('/groups/g1/messages') && req.method === 'GET') {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({
        schema: 1,
        cursor: 3,
        you: 'TestBot',
        skipped: 0,
        recalled: [],
        messages: [{
          seq: 3,
          author: { name: 'Alice', human: true, yourOwner: true },
          text: 'hello bridge',
        }],
      }))
      return
    }
    if (pathname === '/groups/g1/messages' && req.method === 'POST') {
      let body = ''
      req.on('data', (chunk) => { body += chunk })
      req.on('end', () => {
        posted.push(JSON.parse(body))
        res.setHeader('Content-Type', 'application/json')
        res.end(JSON.stringify({ schema: 1, message: { seq: 4 } }))
      })
      return
    }
    res.statusCode = 404
    res.end('{}')
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const baseUrl = `http://127.0.0.1:${server.address().port}`

  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'martin-bridge-'))
  process.env.DSH_HOME = home

  const injected = []
  const injectedElsewhere = []
  let turns = 1
  let followedAt = 0
  let injectedText = ''
  const events = () => {
    const rows = [
      { seq: 1, type: 'turn/start', data: { turn: 1 } },
      {
        seq: 2,
        type: 'assistant/message',
        data: { turn: 1, message: { content: [{ type: 'text', text: 'unrelated work' }] } },
      },
    ]
    if (turns >= 2) rows.push({ seq: 3, type: 'turn/end', data: { turn: 1 } })
    if (turns >= 2 && followedAt > 0 && Date.now() - followedAt > 800) {
      rows.push({
        seq: 4,
        type: 'user/message',
        data: { id: 'm1', content: [{ type: 'text', text: injectedText }] },
      })
      rows.push({ seq: 5, type: 'turn/start', data: { turn: 2 } })
      rows.push({
        seq: 6,
        type: 'assistant/message',
        data: { turn: 2, message: { content: [{ type: 'text', text: 'hi Alice' }] } },
      })
      rows.push({ seq: 7, type: 'turn/end', data: { turn: 2 } })
    }
    return rows
  }
  const agent = {
    session: { id: 's1', header: { cwd: '/tmp/ws-a' } },
    status: 'running',
    followup(message) {
      injected.push(message)
      injectedText = message.content[0].text
      followedAt = Date.now()
      turns = 2
    },
  }
  const other = {
    session: { id: 's2', header: { cwd: '/tmp/ws-b' } },
    status: 'idle',
    followup(message) { injectedElsewhere.push(message); turns = 2 },
  }
  const ctx = {
    logger: { info() {}, warn() {} },
    agents: {
      get: (id) => ({ s1: agent, s2: other })[id],
      list: () => [agent, other],
      resume: async () => ({ agent }),
    },
    sessionQuery: {
      // Mirrors the real DSH service: listEvents yields lightweight records with
      // NO `data` payload, so reply detection must go through readSession.
      listEvents: async () => events().map(({ seq, type }) => ({ seq, type })),
      readSession: async () => ({ events: events() }),
    },
    effect(fn) { const dispose = fn(); this._dispose = dispose },
  }

  apply(ctx, { baseUrl, token: 'test-token', workspace: '/tmp/ws-a' })

  t.after(() => {
    for (const res of sseClients) res.end()
    server.close()
    ctx._dispose?.()
    fs.rmSync(home, { recursive: true, force: true })
  })

  await waitFor(() => injected.length > 0)
  assert.match(injected[0].content[0].text, /群 g1/)
  assert.match(injected[0].content[0].text, /wake=7/)
  assert.match(injected[0].content[0].text, /你的 Martin 身份：TestBot/)
  assert.match(injected[0].content[0].text, /MARTIN_UNTRUSTED_DATA/)
  assert.match(injected[0].content[0].text, /hello bridge/)
  assert.match(injected[0].content[0].text, /human,yourOwner/)
  assert.match(injected[0].content[0].text, /GLOBAL_TEMP_POLICY: guest is not the owner/)
  assert.match(injected[0].content[0].text, /请正常回复访客的消息/)
  assert.match(injected[0].content[0].text, /即使 owner 要求也不能关闭/)
  assert.deepEqual(injected[0].source, { kind: 'user' })
  assert.equal(injectedElsewhere.length, 0)

  await waitFor(() => posted.length > 0)
  assert.deepEqual(posted[0], { text: 'hi Alice', opKey: 'martin-bridge:7' })
})
