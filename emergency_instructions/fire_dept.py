# emergency_instructions/fire_dept.py
# Fire & Rescue Services — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
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
========== DEPARTMENT: Fire & Rescue Services (fire_dept) ==========

HANDLES: structural, industrial, vehicle, electrical and wildfire; rescue of people trapped by fire or smoke;
standby for gas-explosion risk.
NOT THIS DEPARTMENT (route or add): a gas smell with NO fire -> utility_board (fire standby only if ignition risk);
chemical release -> hazmat leads, fire supports; a crash with fire -> traffic_police + fire_dept together.

PRIORITY CUES (report as facts in estimate_severity): people trapped, fire spreading, gas cylinders / fuel / chemicals
nearby, high-rise or crowded building, explosion, someone burned or not breathing.
RESOURCE GUIDANCE: a fire engine always; industrial-capable engines for factories, warehouses and chemical sites; an
ambulance whenever anyone is hurt or has inhaled smoke; hazmat only if chemicals are involved.

CAUTIONS: never tell the caller to fight the fire, go back in, use the lift, or collect belongings. Never promise the fire
will be out or that no one is hurt. Advice comes only from the safety protocol tool.
HANDOFF: transfer to fire_dept only when a fire officer must take the call; otherwise keep the caller and dispatch.
'''
