/* Agent Agora — Shared Animation Engine */
(function(){
'use strict';

/* ── Cursor glow ── */
function initCursorGlow(){
  var el=document.querySelector('.cursor-glow');
  if(!el)return;
  var mx=window.innerWidth/2,my=window.innerHeight/2,cx=mx,cy=my;
  document.addEventListener('mousemove',function(e){mx=e.clientX;my=e.clientY;el.style.opacity='1'});
  document.addEventListener('mouseleave',function(){el.style.opacity='0'});
  (function tick(){
    cx+=(mx-cx)*0.08;cy+=(my-cy)*0.08;
    el.style.left=cx+'px';el.style.top=cy+'px';
    requestAnimationFrame(tick);
  })();
}

/* ── Particle system ── */
function initParticles(){
  var canvas=document.getElementById('particle-canvas');
  if(!canvas)return;
  var ctx=canvas.getContext('2d');
  var particles=[];
  var W,H;
  function resize(){W=canvas.width=window.innerWidth;H=canvas.height=window.innerHeight;}
  resize();
  window.addEventListener('resize',resize);
  var COUNT=55;
  for(var i=0;i<COUNT;i++) particles.push(makeParticle());
  function makeParticle(fromBottom){
    return{
      x:Math.random()*window.innerWidth,
      y:fromBottom?window.innerHeight+10:Math.random()*window.innerHeight,
      r:Math.random()*1.4+0.4,
      vx:(Math.random()-.5)*0.25,
      vy:-(Math.random()*0.4+0.1),
      alpha:Math.random()*0.5+0.15,
      color:Math.random()>.6?'0,217,126':Math.random()>.5?'75,142,245':'167,139,250'
    };
  }
  function draw(){
    ctx.clearRect(0,0,W,H);
    particles.forEach(function(p,i){
      ctx.beginPath();
      ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle='rgba('+p.color+','+p.alpha+')';
      ctx.fill();
      p.x+=p.vx;p.y+=p.vy;
      if(p.y<-10||p.x<-10||p.x>W+10)particles[i]=makeParticle(true);
    });
    requestAnimationFrame(draw);
  }
  draw();
}

/* ── Cinematic logo sweep — 3 logos, random edge-to-edge paths ── */
function initCinematicLogo(){
  var base=document.querySelector('.logo-cinematic');
  if(!base||typeof gsap==='undefined')return;
  var shake=document.querySelector('main')||document.body;

  /* Clone into 3 independent elements */
  var logos=[base];
  for(var i=0;i<2;i++){
    var clone=base.cloneNode(true);
    base.parentNode.appendChild(clone);
    logos.push(clone);
  }

  /* Pick a random point on a given screen edge (with overshoot so it starts off-screen) */
  function edgePt(edge){
    var r=Math.random();
    switch(edge){
      case 'top':    return {x:(r*110-5)+'vw', y:'-20vh'};
      case 'bottom': return {x:(r*110-5)+'vw', y:'115vh'};
      case 'left':   return {x:'-20vw', y:(r*110-5)+'vh'};
      case 'right':  return {x:'115vw', y:(r*110-5)+'vh'};
    }
  }

  /* Build a full random path: enter from one edge, cross the visible screen, exit another */
  function randomPath(){
    var all=['top','bottom','left','right'];
    var si=Math.floor(Math.random()*4);
    var ei=(si+1+Math.floor(Math.random()*3))%4; /* guaranteed different edge */
    var start=edgePt(all[si]);
    var end  =edgePt(all[ei]);
    /* Midpoint sits somewhere in the visible area */
    var mid={x:(Math.random()*60+15)+'vw', y:(Math.random()*50+15)+'vh'};
    return {
      sx:start.x, sy:start.y,
      mx:mid.x,   my:mid.y,
      ex:end.x,   ey:end.y,
      ry:(Math.random()-0.5)*36,
      rx:(Math.random()-0.5)*24
    };
  }

  /* Stagger first fires so all three aren't simultaneous */
  var startDelays=[800,4800,9000];

  logos.forEach(function(el,idx){
    function run(){
      var p=randomPath();
      var tl=gsap.timeline({onComplete:function(){setTimeout(run,Math.random()*3500+5500)}});
      tl.set(el,{x:p.sx,y:p.sy,scale:0.2,opacity:0,filter:'blur(28px)',rotationY:p.ry,rotationX:p.rx,transformPerspective:900})
        .to(el,{opacity:.2,filter:'blur(3px)',scale:1.1,x:p.mx,y:p.my,rotationY:0,rotationX:0,duration:2.6,ease:'power2.in'})
        .to(el,{scale:2.8,x:p.ex,y:p.ey,opacity:.07,filter:'blur(22px)',duration:2.0,ease:'power3.in'},'-=0.5')
        .to(shake,{x:-2,duration:.04,repeat:4,yoyo:true,ease:'none'},'-=1.8')
        .to(el,{opacity:0,duration:.5},'-=0.3');
    }
    setTimeout(run,startDelays[idx]);
  });
}

/* ── Card tilt ── */
function initTilt(){
  document.querySelectorAll('[data-tilt]').forEach(function(card){
    card.addEventListener('mousemove',function(e){
      var r=card.getBoundingClientRect();
      var x=(e.clientX-r.left)/r.width-.5;
      var y=(e.clientY-r.top)/r.height-.5;
      card.style.transform='perspective(900px) rotateX('+(-y*8)+'deg) rotateY('+(x*8)+'deg) translateY(-4px) scale(1.01)';
    });
    card.addEventListener('mouseleave',function(){
      card.style.transform='';
    });
  });
}

/* ── Magnetic buttons ── */
function initMagnetic(){
  document.querySelectorAll('.btn-magnetic').forEach(function(btn){
    btn.addEventListener('mousemove',function(e){
      var r=btn.getBoundingClientRect();
      var x=(e.clientX-r.left-r.width/2)*0.28;
      var y=(e.clientY-r.top-r.height/2)*0.28;
      btn.style.transform='translate('+x+'px,'+y+'px)';
    });
    btn.addEventListener('mouseleave',function(){
      btn.style.transform='';
    });
  });
}

/* ── Ripple clicks ── */
function initRipple(){
  document.querySelectorAll('.ripple-container').forEach(function(btn){
    btn.addEventListener('click',function(e){
      var r=btn.getBoundingClientRect();
      var rip=document.createElement('span');
      rip.className='ripple';
      var size=Math.max(r.width,r.height);
      rip.style.cssText='width:'+size+'px;height:'+size+'px;left:'+(e.clientX-r.left-size/2)+'px;top:'+(e.clientY-r.top-size/2)+'px';
      btn.appendChild(rip);
      setTimeout(function(){rip.remove()},600);
    });
  });
}

/* ── Scroll reveals ── */
function initReveal(){
  var els=document.querySelectorAll('.reveal,.reveal-left,.reveal-scale');
  if(!els.length)return;
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(entry.isIntersecting){
        var delay=parseInt(entry.target.dataset.delay||0,10);
        if(delay){setTimeout(function(){entry.target.classList.add('visible')},delay);}
        else{entry.target.classList.add('visible');}
        io.unobserve(entry.target);
      }
    });
  },{threshold:.08,rootMargin:'0px 0px -20px 0px'});
  els.forEach(function(el){io.observe(el)});
}

/* ── Stagger children (GSAP) — transform only, content always visible ── */
function initStagger(){
  if(typeof gsap==='undefined')return;
  document.querySelectorAll('.stagger-children').forEach(function(parent){
    var delay=parseFloat(parent.dataset.staggerDelay||'.08');
    gsap.from(parent.children,{y:14,duration:.6,stagger:delay,ease:'power3.out',delay:parseFloat(parent.dataset.delay||'0'),clearProps:'transform'});
  });
}

/* ── Hero entrance — handled by CSS animation on index.html, no-op here ── */
function initHeroEntrance(){}

/* ── Animated counters ── */
function initCounters(){
  var els=document.querySelectorAll('[data-count]');
  if(!els.length)return;
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(entry){
      if(!entry.isIntersecting)return;
      var el=entry.target;
      var target=parseFloat(el.dataset.count);
      var suffix=el.dataset.suffix||'';
      var dec=el.dataset.decimals||0;
      var start=0,duration=1600,startTime=null;
      function step(ts){
        if(!startTime)startTime=ts;
        var p=Math.min((ts-startTime)/duration,1);
        var ease=1-Math.pow(1-p,3);
        el.textContent=(start+(target-start)*ease).toFixed(dec)+suffix;
        if(p<1)requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
      io.unobserve(el);
    });
  },{threshold:.5});
  els.forEach(function(el){io.observe(el)});
}

/* ── Nav active indicator ── */
function initNavIndicator(){
  var links=document.querySelectorAll('.nav-link, .nav-links a');
  links.forEach(function(link){
    link.style.transition='color .2s,background .2s';
  });
}

/* ── Boot ── */
function boot(){
  initCursorGlow();
  initParticles();
  initTilt();
  initMagnetic();
  initRipple();
  initReveal();
  initCounters();
  initNavIndicator();
  if(typeof gsap!=='undefined'){
    initCinematicLogo();
    initHeroEntrance();
    initStagger();
  }
}

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',boot);
}else{
  boot();
}
})();
