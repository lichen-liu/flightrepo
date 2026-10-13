"""Contract regressions and comparisons with an independent exhaustive oracle."""
from datetime import datetime, timedelta, timezone
import json
import random

import pytest
from fastapi.testclient import TestClient

from admin import client as admin
from core.connections import Airport, AirportTransfer, RebuildRequest
from core.itineraries import build_itineraries
from core.models import FlightRecord, FlightStatus
from core.planning import rebuild_from_snapshot
from core.routing.debug import replay, replay_document
from core.routing.models import IncompleteSearchError, RouteSearchRequest, RoutingError
from core.routing.search import search_routes
from core.routing.snapshot import RoutingSnapshot
from ingestion.updater import FlightUpdater
from server.api import app, get_store
from server.reader import FlightReader

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def flight(identifier, origin, destination, out, in_at, status=FlightStatus.SCHEDULED):
    return FlightRecord(provider='test', provider_id=str(identifier), flight_number='T'+str(identifier),
        origin_iata=origin, destination_iata=destination, departure_scheduled=BASE+timedelta(minutes=out),
        arrival_scheduled=BASE+timedelta(minutes=in_at), status=status, fetched_at=BASE)


def query(**updates):
    return RouteSearchRequest.model_validate({'origin':'AAA','destination':'EEE','departure_start':BASE,
        'departure_end':BASE+timedelta(days=1),'horizon_end':BASE+timedelta(days=2),'max_legs':4,
        'limit':100,'minimum_connection_minutes':15,**updates})


def identity(legs):
    return tuple((f.provider,f.provider_id) for f in legs)


def airport(code, city):
    return Airport(iata=code,name=code,city_id=city,timezone='UTC',latitude=0,longitude=0)


def oracle_paths(records, request, airports=(), transfers=(), prefix=(), active_window=None):
    # Slow all-record traversal: no production policy, index, session or ranking.
    cities={a.iata:a.city_id for a in airports}
    rules={(t.arrival_iata,t.departure_iata):t.minimum_minutes for t in transfers}
    found=[];avoided=set(getattr(request,'avoid_airports',()))
    seen={f.origin_iata for f in prefix}|{f.destination_iata for f in prefix} if prefix else {request.origin}
    def walk(legs,visited):
        if legs and legs[-1].destination_iata==request.destination:
            found.append(legs);return
        if len(legs)>=request.max_legs:return
        for f in records:
            if f.status==FlightStatus.CANCELLED or f.arrival_scheduled>request.horizon_end:continue
            if f.destination_iata in visited or f.origin_iata in avoided or f.destination_iata in avoided:continue
            if not legs:
                if f.origin_iata!=request.origin or not request.departure_start<=f.departure_scheduled<request.departure_end:continue
            else:
                previous=legs[-1]
                if active_window is not None:
                    minimum=active_window.minimum_minutes
                    maximum=active_window.maximum_minutes if len(legs)==len(prefix) else None
                    not_before=active_window.not_before if len(legs)==len(prefix) else None
                else:
                    minimum,maximum,not_before=request.minimum_connection_minutes,request.maximum_connection_minutes,None
                if f.origin_iata!=previous.destination_iata:
                    if f.origin_iata in visited or not request.allow_airport_change:continue
                    pair=previous.destination_iata,f.origin_iata
                    if pair not in rules or cities.get(pair[0]) is None or cities[pair[0]]!=cities.get(pair[1]):continue
                    minimum=max(minimum,rules[pair])
                gap=(f.departure_scheduled-previous.arrival_scheduled).total_seconds()/60
                if gap<minimum or maximum is not None and gap>maximum or not_before is not None and f.departure_scheduled<not_before:continue
            walk([*legs,f],visited|{f.origin_iata,f.destination_iata})
    walk(list(prefix),seen)
    return found


def oracle_score(legs,ranking):
    count,elapsed=len(legs),(legs[-1].arrival_scheduled-legs[0].departure_scheduled).total_seconds()
    if ranking=='fewest_legs':return count,elapsed,legs[0].departure_scheduled,legs[-1].arrival_scheduled,identity(legs)
    if ranking=='shortest_elapsed':return elapsed,count,legs[-1].arrival_scheduled,legs[0].departure_scheduled,identity(legs)
    return legs[-1].arrival_scheduled,count,elapsed,legs[0].departure_scheduled,identity(legs)


@pytest.mark.parametrize('seed',range(30))
def test_search_matches_oracle_and_input_permutations(seed,tmp_path):
    rng=random.Random(seed);codes=['AAA','BBB','CCC','DDD','EEE']
    airports=[airport(c,'shared' if c in ('BBB','CCC') else c) for c in codes]
    transfers=[AirportTransfer(arrival_iata='BBB',departure_iata='CCC',minimum_minutes=30,source='test')]
    records=[]
    for i in range(45):
        a,b=rng.sample(codes,2);out=rng.randrange(0,600)
        records.append(flight(i,a,b,out,out+rng.randrange(1,90),FlightStatus.CANCELLED if i%11==0 else FlightStatus.SCHEDULED))
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize();updater.upsert(records);updater.update_airports(airports,transfers)
    reader=FlightReader(updater.path)
    for ranking in ('fewest_legs','shortest_elapsed','earliest_arrival'):
        request=query(departure_end=BASE+timedelta(minutes=300),horizon_end=BASE+timedelta(minutes=650),
            ranking=ranking,limit=3,minimum_connection_minutes=0 if seed%3==0 else 15,
            maximum_connection_minutes=None if seed%2 else 120,allow_airport_change=bool(seed%2),
            avoid_airports=['DDD'] if seed%4==0 else [],trace=True,limits={'max_trace_events':15})
        expected=sorted(oracle_paths(records,request,airports,transfers),key=lambda p:oracle_score(p,ranking))[:3]
        original=search_routes(request,RoutingSnapshot(tuple(records),tuple(airports),tuple(transfers)))
        assert [identity(r.legs) for r in original.items]==[identity(p) for p in expected]
        assert original.diagnostics.exhaustive
        stored=reader.search_itineraries(request)
        assert [identity(r.legs) for r in stored.items]==[identity(p) for p in expected]
        assert stored.diagnostics.exhaustive
        rng.shuffle(records);rng.shuffle(airports)
        assert search_routes(request,RoutingSnapshot(tuple(records),tuple(airports),tuple(transfers)))==original


@pytest.mark.parametrize('seed',range(20))
def test_continuations_match_oracle(seed,tmp_path):
    rng=random.Random(seed);prefix=flight('prefix','AAA','BBB',0,30);codes=['AAA','BBB','CCC','DDD','EEE']
    airports=[airport(c,'shared' if c in ('BBB','CCC') else c) for c in codes]
    rules=[AirportTransfer(arrival_iata='BBB',departure_iata='CCC',minimum_minutes=30,source='test')]
    records=[prefix]
    for i in range(70):
        a,b=rng.sample(codes,2);out=rng.randrange(30,700);records.append(flight(i,a,b,out,out+rng.randrange(1,60)))
    request=RebuildRequest(prefix=[{'provider':'test','provider_id':'prefix'}],destination='EEE',max_legs=4,
        horizon_end=BASE+timedelta(days=1),allow_airport_change=True,
        window={'minimum_minutes':15,'maximum_minutes':180,'not_before':BASE+timedelta(minutes=60)})
    expected=oracle_paths(records,request,airports,rules,[prefix],request.window)
    result=rebuild_from_snapshot(request,RoutingSnapshot(tuple(records),tuple(airports),tuple(rules)))
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize();updater.upsert(records);updater.update_airports(airports,rules)
    stored=FlightReader(updater.path).rebuild(request)
    assert identity(stored.legs)==identity(result.legs) and stored.complete==result.complete
    assert stored.diagnostics.exhaustive
    grouped={}
    for path in expected:grouped.setdefault(identity(path)[1],[]).append(path)
    assert {(o.flight.provider,o.flight.provider_id) for o in result.options if o.complete}==set(grouped)
    for option in result.options:
        tails=grouped.get((option.flight.provider,option.flight.provider_id),[])
        assert option.complete==bool(tails)
        if tails:
            best=min(tails,key=lambda p:(p[-1].arrival_scheduled,len(p),identity(p[2:])))
            assert identity([prefix,option.flight,*option.tail])==identity(best)
    if expected:
        earliest=min(grouped,key=lambda key:(next(f.departure_scheduled for f in records if (f.provider,f.provider_id)==key),key))
        best=min(grouped[earliest],key=lambda p:(p[-1].arrival_scheduled,len(p),identity(p[2:])))
        assert identity(result.legs)==identity(best) and result.complete
    else:assert result.legs==[prefix] and not result.complete
    assert result.diagnostics.exhaustive


def test_limit_is_not_traversal_cutoff_and_rankings_explicit():
    records=[flight(i,'AAA','EEE',i*10,900+i*10) for i in range(10)]
    records += [flight('fast','AAA','EEE',150,180),flight('a','AAA','BBB',0,20),flight('b','BBB','EEE',35,60)]
    snapshot=RoutingSnapshot(tuple(records))
    results={mode:search_routes(query(limit=1,ranking=mode),snapshot) for mode in ('fewest_legs','shortest_elapsed','earliest_arrival')}
    assert identity(results['fewest_legs'].items[0].legs)==(('test','fast'),)
    assert identity(results['shortest_elapsed'].items[0].legs)==(('test','fast'),)
    assert identity(results['earliest_arrival'].items[0].legs)==(('test','a'),('test','b'))
    assert results['fewest_legs'].diagnostics.stats.routes_found==12
    assert results['fewest_legs'].diagnostics.result_limit_reached and results['fewest_legs'].diagnostics.exhaustive


def test_departure_connection_and_arrival_boundaries():
    records=[flight('first','AAA','BBB',0,60),flight('at-end','AAA','EEE',120,140),
        flight('min','BBB','EEE',75,100),flight('below','BBB','EEE',74.99,99),flight('max','BBB','EEE',90,110),
        flight('above','BBB','EEE',90.01,111),flight('horizon','BBB','EEE',80,120),flight('past','BBB','EEE',80,120.01)]
    request=query(departure_end=BASE+timedelta(minutes=120),horizon_end=BASE+timedelta(minutes=120),
        minimum_connection_minutes=15,maximum_connection_minutes=30)
    result=search_routes(request,RoutingSnapshot(tuple(records)))
    assert {r.legs[-1].provider_id for r in result.items}=={'min','max','horizon'}


def test_long_flight_connection_outside_initial_window(tmp_path):
    records=[flight('long','AAA','BBB',1430,3600),flight('onward','BBB','EEE',3645,3700)]
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize();updater.upsert(records);reader=FlightReader(updater.path)
    result=build_itineraries(reader,'AAA','EEE',BASE,BASE+timedelta(days=1),max_connection_minutes=60)
    assert identity(result[0].legs)==identity(records)
    request=query(departure_start='2025-12-31T19:00:00-05:00',departure_end='2026-01-01T19:00:00-05:00',horizon_end=BASE+timedelta(days=3))
    assert reader.search_itineraries(request).items[0].legs==records


def test_airport_first_considered_for_short_window_can_be_used_later(tmp_path):
    records=[flight('prefix','AAA','BBB',0,30),flight('to-d','BBB','DDD',45,60),flight('to-c','DDD','CCC',75,90),flight('final','CCC','EEE',200,230)]
    airports=[airport(c,'city' if c in ('BBB','CCC') else c) for c in ['AAA','BBB','CCC','DDD','EEE']]
    rules=[AirportTransfer(arrival_iata='BBB',departure_iata='CCC',minimum_minutes=15,source='test')]
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize();updater.upsert(records);updater.update_airports(airports,rules)
    request=RebuildRequest(prefix=[{'provider':'test','provider_id':'prefix'}],destination='EEE',horizon_end=BASE+timedelta(days=1),
        max_legs=4,allow_airport_change=True,window={'minimum_minutes':15,'maximum_minutes':30})
    result=FlightReader(updater.path).rebuild(request)
    assert result.legs==records and result.complete


def test_ground_transfer_cycle_rejected_in_search_and_prefix():
    records=[flight('one','AAA','BBB',0,30),flight('two','BBB','CCC',45,60),flight('three','BBB','EEE',90,120)]
    airports=[airport(c,'city' if c in ('BBB','CCC') else c) for c in ['AAA','BBB','CCC','EEE']]
    rules=[AirportTransfer(arrival_iata='CCC',departure_iata='BBB',minimum_minutes=15,source='test')]
    result=search_routes(query(allow_airport_change=True,trace=True),RoutingSnapshot(tuple(records),tuple(airports),tuple(rules)))
    assert all(len(r.legs)==2 for r in result.items)
    assert any(e.decision=='airport_cycle' for e in result.diagnostics.events)
    request=RebuildRequest(prefix=[{'provider':'test','provider_id':f.provider_id} for f in records],destination='EEE',
        horizon_end=BASE+timedelta(days=1),allow_airport_change=True)
    with pytest.raises(RoutingError,match='airport_cycle'):rebuild_from_snapshot(request,RoutingSnapshot(tuple(records),tuple(airports),tuple(rules)))


def test_budget_retains_found_route_and_choice_without_claiming_optimality():
    prefix=flight('prefix','AAA','BBB',0,30);first=flight('first','BBB','CCC',45,60)
    tail=flight('tail','CCC','EEE',75,100);other=flight('other','CCC','EEE',90,110)
    request=RebuildRequest(prefix=[{'provider':'test','provider_id':'prefix'}],destination='EEE',max_legs=3,
        chosen_next={'provider':'test','provider_id':'first'},horizon_end=BASE+timedelta(days=1),
        window={'minimum_minutes':15},limits={'max_candidates':2},trace=True)
    result=rebuild_from_snapshot(request,RoutingSnapshot((prefix,first,tail,other)))
    assert result.legs==[prefix,first,tail] and result.complete and result.search_truncated
    assert not result.diagnostics.exhaustive and result.diagnostics.stats.candidates_checked==2
    assert 'partial' in result.message and result.options_total is None


def test_direct_manual_choice_not_charged_twice_and_option_page_keeps_choice():
    prefix=flight('prefix','AAA','BBB',0,30);onward=flight('next','BBB','EEE',75,100)
    request=RebuildRequest(prefix=[{'provider':'test','provider_id':'prefix'}],destination='EEE',
        chosen_next={'provider':'test','provider_id':'next'},horizon_end=BASE+timedelta(days=1),limits={'max_candidates':1})
    result=rebuild_from_snapshot(request,RoutingSnapshot((prefix,onward)))
    assert result.complete and result.diagnostics.exhaustive and result.options_total==1
    records=[prefix,*[flight(i,'BBB','EEE',90+i,120+i) for i in range(8)]]
    request=RebuildRequest(prefix=request.prefix,destination='EEE',chosen_next={'provider':'test','provider_id':'7'},
        horizon_end=request.horizon_end,option_limit=2)
    result=rebuild_from_snapshot(request,RoutingSnapshot(tuple(records)))
    assert result.complete and len(result.options)==2 and result.options[-1].flight.provider_id=='7'
    assert result.options_total==8 and result.options_truncated and result.diagnostics.exhaustive


def test_trace_bounded_identified_and_replay_detects_changed_facts():
    snapshot=RoutingSnapshot(tuple(flight(i,'AAA','EEE',i,100+i) for i in range(8)))
    request=query(trace=True,limits={'max_candidates':5,'max_trace_events':2});result=search_routes(request,snapshot)
    assert not result.diagnostics.exhaustive and len(result.diagnostics.events)==2
    assert result.diagnostics.stats.trace_events_omitted==3 and result.diagnostics.stats.candidates_checked==5
    document=replay_document('search',request,snapshot,result)
    assert replay(json.loads(json.dumps(document)))['matches_saved_result']
    document['snapshot']['flights'][0]['flight_number']='CHANGED'
    assert not replay(document)['matches_saved_result']
    assert search_routes(query(),snapshot).diagnostics.events==[]


def test_conflicting_identities_rejected_and_identical_duplicates_deduplicated():
    f=flight('one','AAA','EEE',0,60)
    with pytest.raises(RoutingError,match='conflicting records'):search_routes(query(),RoutingSnapshot((f,f.model_copy(update={'flight_number':'different'}))))
    with pytest.raises(RoutingError,match='conflicting metadata'):RoutingSnapshot((f,),(airport('AAA','one'),airport('AAA','two')))
    assert search_routes(query(),RoutingSnapshot((f,f))).items[0].legs==[f]


def test_catalog_and_flights_one_snapshot_during_update(tmp_path,monkeypatch):
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize()
    f=flight('one','AAA','EEE',0,60);updater.upsert([f]);updater.update_airports([airport('AAA','old')],[])
    reader=FlightReader(updater.path);original=reader._catalog
    def update_after_catalog(connection):
        data=original(connection);updater.upsert([f.model_copy(update={'status':FlightStatus.CANCELLED})])
        updater.update_airports([airport('AAA','new')],[]);return data
    monkeypatch.setattr(reader,'_catalog',update_after_catalog)
    snapshot=reader.routing_snapshot(query());fresh=FlightReader(updater.path).routing_snapshot(query())
    assert snapshot.flights==(f,) and snapshot.airports[0].city_id=='old'
    assert not fresh.flights and fresh.airports[0].city_id=='new' and fresh.snapshot_id!=snapshot.snapshot_id


def test_index_api_partial_results_and_naive_times(tmp_path):
    updater=FlightUpdater(tmp_path/'f.db');updater.initialize();updater.upsert([flight(i,'AAA','EEE',i,100+i) for i in range(5)])
    reader=FlightReader(updater.path)
    with reader.connect() as conn:
        plan=conn.execute("EXPLAIN QUERY PLAN SELECT * FROM flights WHERE origin_iata=? AND departure_scheduled>=? AND departure_scheduled<? AND status != 'cancelled' ORDER BY departure_scheduled,provider,provider_id LIMIT 10",('AAA',BASE.isoformat(),(BASE+timedelta(days=1)).isoformat())).fetchall()
        assert any('idx_flights_routing' in str(tuple(row)) for row in plan)
    app.dependency_overrides[get_store]=lambda:reader
    try:
        with TestClient(app) as client:
            response=client.post('/v1/itineraries/search',json=query(limits={'max_records':1},trace=True).model_dump(mode='json'))
            assert response.status_code==200 and response.json()['diagnostics']['termination_reasons']==['data_limit']
            assert not response.json()['diagnostics']['exhaustive']
            for endpoint in ('flights','routes','itineraries'):
                response=client.get('/v1/'+endpoint,params={'origin':'AAA','destination':'EEE','start':'2026-01-01T00:00:00','end':'2026-01-02T00:00:00Z'})
                assert response.status_code==422
    finally:app.dependency_overrides.clear()


def test_legacy_list_api_refuses_unproven_rankings(tmp_path):
    class PartialReader(FlightReader):
        def search_itineraries(self,request):return search_routes(request,RoutingSnapshot((flight('one','AAA','EEE',0,60),),data_truncated=True))
    reader=PartialReader(tmp_path/'unused.db')
    with pytest.raises(IncompleteSearchError):build_itineraries(reader,'AAA','EEE',BASE,BASE+timedelta(days=1))
    app.dependency_overrides[get_store]=lambda:reader
    try:
        with TestClient(app) as client:
            response=client.get('/v1/itineraries',params={'origin':'AAA','destination':'EEE','start':BASE.isoformat(),'end':(BASE+timedelta(days=1)).isoformat()})
            assert response.status_code==409 and response.json()['detail']['code']=='incomplete_search'
    finally:app.dependency_overrides.clear()


def test_replay_cli_never_opens_database(tmp_path,monkeypatch,capsys):
    request=query();snapshot=RoutingSnapshot((flight('one','AAA','EEE',0,60),));result=search_routes(request,snapshot)
    saved=tmp_path/'replay.json';saved.write_text(json.dumps(replay_document('search',request,snapshot,result)))
    monkeypatch.setattr(admin,'get_settings',lambda:(_ for _ in ()).throw(AssertionError('DB settings should not be read')))
    admin.run(['replay-routing',str(saved)])
    assert json.loads(capsys.readouterr().out)['matches_saved_result']


@pytest.mark.parametrize('updates',[
    {'departure_start':'2026-01-01T00:00:00'},{'origin':'?AA'},{'origin':'AAA','destination':'aaa'},
    {'minimum_connection_minutes':50,'maximum_connection_minutes':30},{'avoid_airports':['AAA']},
    {'horizon_end':BASE+timedelta(days=100)},{'limits':{'max_candidates':999999999}},
    {'departure_start':'0001-01-01T00:00:00+14:00'},
])
def test_invalid_query_rejected(updates):
    with pytest.raises(ValueError):query(**updates)
