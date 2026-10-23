// ===== 新聞 × 個股比對引擎 =====
// 詞庫（題材、族群、簡稱、易混淆公司名）放在 lexicon.json，頁面與更新腳本共用。
let THEMES = {}, GROUPS = {}, ALIAS = {}, AMBIG = new Set();

function setLexicon(lex){
  THEMES = lex.themes || {};
  GROUPS = lex.groups || {};
  ALIAS = lex.alias || {};
  AMBIG = new Set(lex.ambiguous || []);
}

function buildMatcher(universe){
  const byCode = {}; const names = [];
  universe.forEach(([code,name,mk,ind])=>{
    byCode[code] = {code,name,mk,ind};
    const base = name.replace(/\*|-KY|-創|創$/g,"");
    [name, base].forEach(n=>{ if(n.length>=2 && !AMBIG.has(n)) names.push([n,code]); });
  });
  Object.entries(ALIAS).forEach(([n,c])=>names.push([n,c]));
  Object.keys(GROUPS).forEach(n=>names.push([n,"G:"+n]));
  const seen = new Set(); const uniq = [];
  names.sort((a,b)=>b[0].length-a[0].length).forEach(x=>{ if(!seen.has(x[0])){seen.add(x[0]);uniq.push(x);} });
  return {byCode, names:uniq};
}

function matchText(text, M){
  const codes = new Map();
  const taken = new Array(text.length).fill(false);
  // 1) 明確代號：「名稱（1234）」
  text.replace(/[（(](\d{4})[）)]/g,(m,c)=>{ if(M.byCode[c]) codes.set(c,"代號"); return m; });
  // 2) 最長名稱優先，已被長名稱占用的字不再比對（避免「南亞科」又配到「南亞」）
  for(const [n,c] of M.names){
    let i = text.indexOf(n);
    while(i>=0){
      let free = true; for(let k=i;k<i+n.length;k++) if(taken[k]){free=false;break;}
      if(free){
        for(let k=i;k<i+n.length;k++) taken[k]=true;
        if(c.startsWith("G:")) (GROUPS[c.slice(2)]||[]).forEach(x=>{ if(!codes.has(x)) codes.set(x,"族群:"+c.slice(2)); });
        else if(!codes.has(c)) codes.set(c,"名稱");
      }
      i = text.indexOf(n,i+n.length);
    }
  }
  const themes = Object.entries(THEMES).filter(([k,ws])=>ws.some(w=>text.includes(w))).map(([k])=>k);
  return {codes:[...codes.entries()].map(([c,how])=>({c,how})), themes};
}
if(typeof module!=="undefined") module.exports={setLexicon,buildMatcher,matchText,get THEMES(){return THEMES;}};
