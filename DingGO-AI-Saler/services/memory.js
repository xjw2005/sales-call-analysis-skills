const config = require('../config/index');
const { request } = require('./request');

const off = () => config.useMock || config.mockAI;

async function listMemories() {
  if (off()) return { items: [] };
  return request({ url: '/memories' });
}
const addMemory = (data) => request({ url: '/memories', method: 'POST', data });
const confirmMemory = (id) => request({ url: `/memories/${id}/confirm`, method: 'POST' });
const updateMemory = (id, data) => request({ url: `/memories/${id}`, method: 'PATCH', data });
const deleteMemory = (id) => request({ url: `/memories/${id}`, method: 'DELETE' });
const shareMemory = (id, team) => request({ url: `/memories/${id}/team`, method: 'POST', data: { team } });

module.exports = { listMemories, addMemory, confirmMemory, updateMemory, deleteMemory, shareMemory };
