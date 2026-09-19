# emergency_instructions/forest_dept.py
# Forest Department / Animal Control — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
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
========== DEPARTMENT: Forest Department / Animal Control (forest_dept) ==========

HANDLES: wild animals in populated areas, injured or trapped wildlife, and livestock accidents.
NOT THIS DEPARTMENT: a person bitten or injured -> ems as well; an animal on a road causing a crash -> traffic_police as well;
an angry crowd around the animal -> police for crowd control.

PRIORITY CUES: is the animal aggressive or cornered, is anyone hurt, is it near children, schools or a crowd, is it
blocking a road.
RESOURCE GUIDANCE: a rescue team for wildlife handling; a police unit for crowd or traffic control.

CAUTIONS: never tell the caller to approach, corner, feed, photograph up close, or try to catch the animal. Keep people and
pets indoors with doors closed, and keep well back. Do not describe the animal as harmless.
'''
