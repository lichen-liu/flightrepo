"""Build the R06 fragment from preserved R05 and editable additions; no network."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs/design"


def build():
    text = (DESIGN / "flyji-refined.html").read_text()
    text = text.replace("flyji-refined-root", "flyji-connections-root")
    a = text.index('<div class="viz-carousel"')
    b = text.index("</div>", a) + len("</div>")
    text = (
        text[:a]
        + """<div class="viz-carousel" aria-label="Flyji connection layouts">
<section data-variant="Globe first" data-direction="orbit" aria-label="Globe and connection editor"></section>
<section data-variant="Map first" data-direction="air" aria-label="Map and connection editor" hidden></section>
<section data-variant="Connections first" data-direction="canvas" aria-label="Connection editor and schedule" hidden></section>
</div>"""
        + text[b:]
    )
    text = text.replace(
        "</style>",
        (DESIGN / "assets/connection-editor.css").read_text() + "\n</style>",
        1,
    )
    fixture = (ROOT / "fixtures/connections-demo.json").read_text()
    text = text.replace(
        "<script>\n(()=>{",
        '<script type="application/json" id="fr-connection-data">'
        + fixture
        + "</script>\n<script>\n(()=>{",
        1,
    )
    text = text.replace(
        "const state={...defaults,...(saved.refreshVersion===1?saved.query:{})};",
        "const state={...defaults,...(saved.connectionVersion===1?saved.query:{})};\n"
        + (DESIGN / "assets/connection-editor.js").read_text(),
    )
    text = text.replace(
        "get=id=>all.find(o=>o.id===id)",
        "get=id=>state.edited?.id===id?state.edited:all.find(o=>o.id===id)",
    )
    text = text.replace(
        "privateContent:{refreshVersion:1,query:state",
        "privateContent:{connectionVersion:1,refreshVersion:1,query:state",
    )
    text = text.replace(
        "mobileViews:Object.fromEntries",
        "mapKinds:Object.fromEntries(models.map(m=>[m.direction,m.mapKind])),mobileViews:Object.fromEntries",
    )
    text = text.replace("if(p?.refreshVersion!==1)return", "if(p?.connectionVersion!==1)return")
    # Standard headings, with no invitation, slogan or decorative prose.
    text = text.replace(
        "const headings={air:['A little more possibility.','See the route. Feel the shape of the journey.'],canvas:['Make room for the journey.','Explore the dates, the flights, and the hours in between.'],orbit:['A different perspective.','Follow your journey across the Earth, one moment at a time.']};",
        "const headings={air:['Flight routes'],canvas:['Flight connections'],orbit:['Flight routes']};",
    )
    text = re.sub(
        r'<div class="fr-lead"><div class="fr-kicker">.*?</p></div>\$\{query\}',
        '<div class="fr-lead"><h1>${headings[direction][0]}</h1></div>${query}',
        text,
        count=1,
    )
    text = text.replace(
        "${direction==='orbit'?'The Earth, connected.':'Across the Atlantic.'}",
        "Route map",
    )
    text = text.replace("Your possibilities", "Flight options").replace(
        "One clock. Two choices.", "Flight schedule"
    )
    text = text.replace("compared?'Flight schedule':'Time in your journey.'", "'Flight schedule'")
    text = text.replace(
        "Flight exploration<br>Illustrative schedules · not live",
        "Route explorer · demo schedules",
    )
    text = text.replace("Great-circle routes · geographic view", "Flights and airport changes")
    text = text.replace("Next-day dates stay visible", "UTC").replace(
        "Schedule cursor ·", "Schedule time ·"
    )
    text = text.replace(
        "Unpriced exploration · transfer requirements unknown",
        "Transfer allowances are demo assumptions",
    )
    text = text.replace("This day needs more evidence.", "No data loaded.").replace(
        "Flight details &amp; evidence", "Flight details"
    )
    text = text.replace('["map","options","time"]', '["map","legs","options","time"]')
    text = text.replace(
        '${view==="map"?"Map":view==="options"?"Options":"Time"}',
        '${view==="map"?"Map":view==="legs"?"Connections":view==="options"?"Options":"Schedule"}',
    )
    text = text.replace(
        "const detail=`",
        'const editor=`<section class="fr-editor" id="${ident}-legs" aria-label="Editable flight connections"></section>`;\n const detail=`',
        1,
    )
    text = text.replace(
        "time+choices+map+detail:direction==='orbit'?map+choices+time+detail:map+choices+time+detail",
        "editor+map+time+choices+detail:direction==='orbit'?map+editor+time+detail+choices:map+editor+choices+time+detail",
    )
    text = text.replace(
        "const model={direction,ident,app,camera",
        "const model={direction,ident,app,mapKind:saved.mapKinds?.[direction]||(direction==='orbit'?'globe':'flat'),camera",
    )
    text = text.replace(
        "Object.assign(m.camera,p.cameras?.[m.direction]||{});",
        "Object.assign(m.camera,p.cameras?.[m.direction]||{});m.mapKind=p.mapKinds?.[m.direction]||m.mapKind;",
    )
    marker = "models.push(model);"
    text = text.replace(
        marker,
        marker
        + """
  const modebar=document.createElement('div');modebar.className='fr-map-modes';modebar.setAttribute('aria-label','Map projection');
  modebar.innerHTML='<button type="button" class="cursor-interaction" data-map-kind="flat">Flat map</button><button type="button" class="cursor-interaction" data-map-kind="globe">Globe</button>';
  app.querySelector('.fr-map-tools').before(modebar);
  for(const button of modebar.querySelectorAll('button'))button.onclick=()=>{model.mapKind=button.dataset.mapKind;model.lastGeometry=null;drawMap(model);persist(direction);};
""",
        1,
    )
    # Camera, drawing and recovery depend on projection, not the layout alternative.
    for start, end in [
        ("function fitCamera(model)", "function updateVisibility(model)"),
        ("function updateVisibility(model)", "function airportLabels("),
        ("function drawMap(model", "function renderMapInspect(model)"),
    ]:
        a, b = text.index(start), text.index(end)
        region = (
            text[a:b]
            .replace("direction==='orbit'", "model.mapKind==='globe'")
            .replace("direction!=='orbit'", "model.mapKind!=='globe'")
        )
        text = text[:a] + region + text[b:]
    text = text.replace("else if(direction==='orbit'){", "else if(model.mapKind==='globe'){")
    text = text.replace(
        "world:direction==='orbit'?false:true",
        "world:model.mapKind==='globe'?false:true",
    )
    text = text.replace(
        "if(direction==='orbit'){camera.lon", "if(model.mapKind==='globe'){camera.lon"
    )
    text = text.replace(
        "if(direction==='orbit'){if(action", "if(model.mapKind==='globe'){if(action"
    )
    text = text.replace("camera.world?d3.geoNaturalEarth1()", "camera.world?d3.geoNaturalEarth1()")
    text = text.replace(
        "[width,height,camera,edges.map", "[width,height,model.mapKind,camera,edges.map"
    )
    text = text.replace(
        "const edges=routeEdges();const geometry=",
        "for(const b of app.querySelectorAll('[data-map-kind]'))b.setAttribute('aria-pressed',String(b.dataset.mapKind===model.mapKind));const edges=routeEdges();const geometry=",
    )
    text = text.replace(
        "model.projection=projection;model.edges=edges;",
        "model.projection=projection;model.edges=edges;app.querySelector('[data-camera=world]').textContent=model.mapKind==='globe'?'Atlantic view':'World';const groundKey=app.querySelector('.fr-ground-key');if(groundKey)groundKey.hidden=!edges.some(e=>e.transfer);",
    )
    text = text.replace("const key=leg.from+'|'+leg.to;", "const key=leg.from+'|'+leg.to;")
    text = text.replace(
        "return [...edges.values()];}",
        """for(const o of [selected,compared].filter(Boolean))for(let i=1;i<o.legs.length;i++){const a=o.legs[i-1].to,b=o.legs[i].from;if(a===b)continue;const key=a+'|'+b;edges.set(key,{key,from:a,to:b,geometry:line({from:a,to:b}),journeys:[],selected:o.id===state.selected,compared:o.id===state.compared,transfer:true});}return [...edges.values()];}""",
    )
    text = text.replace(
        "fr-route ${e.selected&&e.compared",
        "fr-route ${e.transfer?'transfer ':''}${e.selected&&e.compared",
    )
    text = text.replace(
        "for(const model of models){renderControls(model);renderChoices(model);renderDetail(model);",
        "for(const model of models){renderControls(model);renderChoices(model);renderEditor(model);renderDetail(model);",
    )
    text = text.replace(
        '<div class="fr-map-hint"></div>',
        '<span class="fr-ground-key" hidden><i class="fr-transfer-key"></i>Airport change</span><div class="fr-map-hint"></div>',
    )
    text = text.replace(
        "const next=o.legs[i+1];if(next&&state.hour",
        "const next=o.legs[i+1];if(next&&next.from!==l.to&&state.hour>=l.inAt&&state.hour<next.out)return {name:'Airport transfer window · '+l.to+' → '+next.from,at:places[l.to].coord,kind:'wait'};if(next&&state.hour",
    )
    text = text.replace(
        "return {name:'Arrived · Paris · CDG',at:places.CDG.coord}",
        "return {name:o.legs.at(-1).to==='CDG'?'Arrived · CDG':'Awaiting continuation · '+o.legs.at(-1).to,at:places[o.legs.at(-1).to].coord}",
    )
    text = text.replace(
        "const route=o=>[o.legs[0].from,...o.legs.map(l=>l.to)].join(' → ');",
        "const route=o=>o.legs.map((l,i)=>(i&&o.legs[i-1].to!==l.from?' ⇢ '+l.from:i?'':' '+l.from)+' → '+l.to).join('').trim();",
    )
    text = text.replace(
        "const outside=!pool().some(x=>x.id===o.id)",
        "const outside=o.id===state.edited?.id?(o.day!==+state.day||state.nonstop&&o.legs.length>1||state.avoid&&o.legs.some(l=>l.from===state.avoid||l.to===state.avoid)||o.legs.at(-1).to==='CDG'&&o.inAt>deadlineHour()):!pool().some(x=>x.id===o.id)",
    )
    text = text.replace("matching dated journeys ·", "matching preset routes ·")
    text = text.replace(
        " loaded options · ${c} fixture coverage",
        " preset routes · ${c} preset coverage",
    )
    text = text.replace(
        "${endpoints(o)}${flightIdentity(o)}",
        "${endpoints(o)}${o.legs.at(-1).to!=='CDG'?'<p>Destination not reached · continuation needed</p>':''}${flightIdentity(o)}",
    )
    text = text.replace(
        "function endpoints(o){return",
        "function endpoints(o){const end=o.legs.at(-1).to;return",
    )
    text = text.replace(
        "Arrive Paris · CDG</span><strong>${local(o.inAt,'CDG',false)}",
        "${end==='CDG'?'Arrive Paris · CDG':'Last arrival · '+end}</span><strong>${local(o.inAt,end,false)}",
    )
    text = text.replace(
        "${shortDate(o.inAt,'CDG')} · CEST</small></div></div>",
        "${shortDate(o.inAt,end)} · ${places[end].zone}</small></div></div>",
    )
    text = text.replace("${local(j.inAt,'CDG')}", "${local(j.inAt,j.legs.at(-1).to)}")
    # Empty compare state: remove the automated sentence; the action is in Flight options.
    text = text.replace("Choose Compare on a different journey to align its flights and waits.", "")
    text = text.replace(
        "${o.legs.length>1?' · '+duration(o.legs[1].out-o.legs[0].inAt)+' connection wait':''}",
        "${o.legs.length>1?' · '+duration(o.legs.slice(1).reduce((sum,l,i)=>sum+l.out-o.legs[i].inAt,0))+' total connection time':''}",
    )
    text = text.replace(
        "compared&&o?`${label(compared)}",
        "compared&&o?(o.legs.at(-1).to!=='CDG'||compared.legs.at(-1).to!=='CDG'?'Route incomplete · final arrival cannot be compared':`${label(compared)}",
    )
    text = text.replace(
        " · retained outside filters':''}`:'';",
        " · retained outside filters':''}`):'';",
    )
    text = text.replace(
        "'Route lines show geography; dated choices show the complete journey.'",
        "'Scheduled routes · select an airport or flight leg.'",
    )
    text = text.replace(
        "'Selected and compared journeys share geographic legs. Their markers show different scheduled moments.'",
        "'Shared route · markers show different scheduled times.'",
    )
    (DESIGN / "flyji-connections.html").write_text(text)
    assert len(text.encode()) < 1_000_000
    return DESIGN / "flyji-connections.html"


if __name__ == "__main__":
    print(build())
