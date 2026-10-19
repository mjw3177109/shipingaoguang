(() => {
  "use strict";
  const img=document.getElementById("label-image"), canvas=document.getElementById("ball-canvas");
  const video=document.getElementById("label-video"), clock=document.getElementById("ball-time");
  const absent=document.getElementById("ball-absent"), msg=document.getElementById("frame-message");
  if(!img||!canvas||!video||!clock)return;
  const ctx=canvas.getContext("2d");
  let start=null,box=null;
  function sync(){canvas.width=img.clientWidth;canvas.height=img.clientHeight;redraw();}
  function redraw(){ctx.clearRect(0,0,canvas.width,canvas.height);
    if(box){ctx.strokeStyle="#f9a51b";ctx.lineWidth=3;
      ctx.strokeRect(box.x*canvas.width,box.y*canvas.height,
                     box.w*canvas.width,box.h*canvas.height);}}
  function put(box){for(const k of ["x","y","w","h"])
    document.getElementById("ball-"+k).value=box?(Math.round(box[k]*100000)/100000).toString():"";
    redraw();}
  function point(e){const r=canvas.getBoundingClientRect();
    return{x:Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),
           y:Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))};}
  function rect(a,b){return{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),
    w:Math.abs(a.x-b.x),h:Math.abs(a.y-b.y)};}
  function loadFrame(time, existing){if(!Number.isFinite(time)||time<0){msg.textContent='请输入有效的时间';return;}
    absent.checked=Boolean(existing&&existing.absent);
    box=existing&&!existing.absent?existing.box:null;
    put(box);clock.value=time.toFixed(2);
    img.src=window.ballFrameUrl+"?t="+encodeURIComponent(clock.value);
    msg.textContent="当前帧："+clock.value+" 秒；框选篮球或选无球。";
  }
  window.loadLabeledFrame=loadFrame;
  window.reviewBallFrame=(time,x,y,w,h,isAbsent)=>{
    loadFrame(Number(time),{absent:Boolean(isAbsent),
      box:{x:Number(x),y:Number(y),w:Number(w),h:Number(h)}});
    document.getElementById("ball-canvas").scrollIntoView({behavior:"smooth",block:"center"});
  };
  document.getElementById("use-video-time").addEventListener("click",()=>{clock.value=video.currentTime.toFixed(2);});
  document.getElementById("load-frame").addEventListener("click",()=>loadFrame(Number(clock.value)));
  img.addEventListener("load",sync);
  img.addEventListener("error",()=>{msg.textContent="画面载入失败：检查时间是否在视频有效范围内。";});
  window.addEventListener("resize",sync);
  canvas.addEventListener("pointerdown",e=>{if(absent.checked)return;start=point(e);box=rect(start,start);canvas.setPointerCapture(e.pointerId);redraw();});
  canvas.addEventListener("pointermove",e=>{if(start){box=rect(start,point(e));redraw();}});
  canvas.addEventListener("pointerup",e=>{if(!start)return;box=rect(start,point(e));start=null;
    if(box.w<.003||box.h<.003){box=null;msg.textContent="篮球框太小，请重新框选。";}
    put(box);});
  canvas.addEventListener("pointercancel",()=>{start=null;});
  absent.addEventListener("change",()=>{if(absent.checked){box=null;put(null);}});
  document.getElementById("label-form").addEventListener("submit",e=>{
    if(!absent.checked && (!box||box.w<.003||box.h<.003)){e.preventDefault();alert("请在图片上框住篮球，或选择本帧无球。");}});
  const requested=new URLSearchParams(window.location.search).get("t");
  if(requested!==null && Number.isFinite(Number(requested)) && Number(requested)>=0) {
    loadFrame(Number(requested));
  }
  requestAnimationFrame(sync);
})();