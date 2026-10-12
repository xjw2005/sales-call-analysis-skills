// 内存缓存：同一份数据在有效期内多个页面共用，不重复请求；同时在请求中的多个调用共用一次请求。
// 只放在内存里（小程序被关掉后失效）：数据量大（上千家门店）时不适合写本机存储。
//   peek()            立刻取缓存里的值（可能已过期，用来先显示旧数据），没有就返回 null
//   get({ fresh })    有效期内直接用缓存；过期了才请求。fresh 为 true 时强制重新请求，失败会抛错；
//                     不强制时，请求失败但手里有旧数据，就先用旧数据（弱网下不要弹出空白）
//   invalidate()      标记为过期（数据变了，比如新建了门店）：旧值还能 peek，但下次 get 必须重新请求
const entries = {};

function create(name, ttlMs, loader) {
  const e = { value: null, at: 0, pending: null, gen: 0, ttlMs, loader };
  entries[name] = e;
  return {
    peek: () => e.value,
    get({ fresh = false } = {}) {
      if (!fresh && e.value && Date.now() - e.at < e.ttlMs) return Promise.resolve(e.value);
      if (e.pending && !fresh) return e.pending;
      const gen = e.gen;
      const p = Promise.resolve().then(e.loader).then((value) => {
        if (gen === e.gen) { e.value = value; e.at = Date.now(); } // 请求期间数据又变了：这份结果已经旧了，不存
        return value;
      }, (err) => {
        if (!fresh && e.value) return e.value;
        throw err;
      }).finally(() => { if (e.pending === p) e.pending = null; });
      e.pending = p;
      return p;
    },
  };
}

// 数据变了：下次必须重新请求（旧值还能用来先显示）
function invalidate(name) {
  const e = entries[name];
  if (!e) return;
  e.at = 0;
  e.gen += 1;
  e.pending = null;
}

// 换了登录的人：所有缓存作废，连旧值也不能再显示
function clearAll() {
  Object.keys(entries).forEach((name) => {
    invalidate(name);
    entries[name].value = null;
  });
}

module.exports = { create, invalidate, clearAll };
