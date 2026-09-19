# emergency_instructions/utility_board.py
# Electricity / Gas / Water Board — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
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
========== DEPARTMENT: Electricity / Gas / Water Board (utility_board) ==========

HANDLES: power lines down, gas pipeline leaks, water-main bursts, transformer fires or sparking.
NOT THIS DEPARTMENT: any fire or explosion -> fire_dept leads; people burned or shocked -> ems; an industrial gas release
-> hazmat.

PRIORITY CUES: a person in contact with a wire, sparking or fire, a strong gas smell indoors, an enclosed space, a crowded
area, a water main flooding a road or basement.
RESOURCE GUIDANCE: utility crews are reached through the department alert (no unit in the fleet); fire engine on standby
when there is fire or explosion risk; rescue only if people are at risk.

CAUTIONS: at least ten metres from any fallen wire, never touch anything in contact with it, never use water on it. For gas:
leave the area, no switches, flames or phones near the leak, do not start a vehicle. Only mention shutting a valve or
breaker if the caller says they know how and it is safe — never instruct it.
'''
