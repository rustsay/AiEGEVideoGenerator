"""JS-движок плеера, который мы вставляем в сгенерированный HTML.

Ведёт себя как видеоплеер: автостарт при загрузке, шаги сменяются
по таймеру (dur из steps либо оценка по длине текста), прогресс-бар,
клавиатура. window.__engine — хук для рекордера MP4.
"""
import json

PLAYER_JS = r"""
(function(){
var RAW='__STEPS__';
var steps;
try{steps=JSON.parse(RAW)}catch(e){steps=[]}
steps.forEach(function(s){
  if(!s.dur){
    var w=(s.text||'').split(/\s+/).filter(Boolean).length;
    s.dur=Math.min(14,Math.max(4.5,2+w*0.38));
  }
});
var cur=-1,timer=null;
function $(id){return document.getElementById(id)}
function apply(n){
 cur=n;
 document.querySelectorAll('#boardSvg .fade').forEach(function(e){e.classList.remove('on')});
 if(n>=0){
  for(var i=0;i<=n;i++)(steps[i].show||[]).forEach(function(id){var e=$(id);if(e)e.classList.add('on')});
  document.querySelectorAll('#boardSvg .path').forEach(function(e){e.classList.add('on')});
 }
 var st=$('status'),bar=$('bar');
 if(st)st.textContent=n>=0?('Шаг '+(n+1)+' / '+steps.length):'';
 if(bar)bar.style.width=(n>=0?((n+1)/steps.length*100):0)+'%';
}
function stop(){if(timer){clearTimeout(timer);timer=null}}
function next(){
 if(cur>=steps.length-1){stop();if(window.__onEnd)window.__onEnd();return true}
 apply(cur+1);return false;
}
function playFrom(n){
 stop();apply(n);
 var tick=function(){
  timer=setTimeout(function(){
   if(!next())tick();
  },cur<0?600:(steps[cur].dur||6)*1000);
 };
 tick();
}
document.addEventListener('keydown',function(e){
 if(e.code==='ArrowRight'||e.code==='Space'){e.preventDefault();stop();next()}
 if(e.code==='ArrowLeft'){e.preventDefault();stop();apply(Math.max(-1,cur-1))}
});
window.__engine={
 total:steps.length,
 get:function(){return cur},
 goto:function(n){stop();apply(n)},
 playFrom:playFrom,
 stop:stop
};
apply(-1);
var STILL=new URLSearchParams(location.search).has('still');
if(STILL)apply(0);
else setTimeout(function(){playFrom(0)},350);
})();
"""


def build_player_js(steps: list[dict]) -> str:
    """Готовый JS со вшитым списком шагов."""
    safe = []
    for i, s in enumerate(steps):
        show = s.get("show") or ["step%d" % k for k in range(1, i + 2)]
        safe.append({
            "title": s.get("title", ""),
            "text": s.get("text", ""),
            "show": show,
            "dur": float(s["dur"]) if s.get("dur") else None,
        })
    raw = json.dumps(safe, ensure_ascii=False).replace("</", "<\\/")
    return PLAYER_JS.replace("__STEPS__", raw)
