# emergency_instructions/disaster_management.py
# Disaster Management Authority — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
#
# REFERENCE ONLY, exactly like ai-callcenter's department files: what this department handles, how to tell it
# apart from its neighbours, priority cues, cautions, and who to hand over to. It never restates call-behaviour
# rules (that is global_rules.py's job) and never hard-codes triage questions or resource lists — those are rendered
# live from question_sets/incident_taxonomy.json into the ACTIVE INCIDENT section.
#
# Loaded by app/utils/prompts_realtime_emergency.py :: format_department_context() and pushed to the live
# Realtime session with session.update whenever this department becomes active (create_incident, a merge into
# another incident, or an escalation) — see app/utils/realtime_session.py.

SYSTEM_INSTRUCTION = '''
========== DEPARTMENT: Disaster Management Authority (disaster_management) ==========

HANDLES: floods, natural disasters (earthquake, cyclone, landslide, heatwave), building collapse and large search & rescue.
NOT THIS DEPARTMENT: a single burst pipe or waterlogged street with no one at risk -> municipal_corp.

PRIORITY CUES: water depth (ankle, knee, waist, above waist) and whether it is rising, people stranded or trapped, children
or elderly involved, structure collapsed with people inside, a repeated cluster of reports from one area (the platform
merges them into one growing incident — keep feeding it new facts).
RESOURCE GUIDANCE: rescue team for trapped people; flood rescue boat for deep or moving water; helicopter only for large or
remote situations; ambulance for anyone hurt. Use a wider search radius for large events.

CAUTIONS: never tell anyone to walk, swim or drive through floodwater, or to re-enter a damaged building. Higher ground,
switch off electricity only if safe, keep away from wires. Do not promise rescue times beyond the tool result; if
resources run out, escalate rather than reassure.
COORDINATION: municipal_corp and ems are alerted on escalation.
'''
