# Router prerequisites for PCE-initiated RSVP-TE LSPs (IOS XRv 6.1.2)

The app's add-lsp only works if the head-end (R1) accepts PCE instantiation. Compare this with
your GA5 configuration; adapt the interface names and the ODL address. **Check every command
on your XRv image**: syntax differs between XR releases.

```
! all routers - MPLS-TE on the mesh links (OSPF shown; use ISIS equivalents if GA5 used ISIS)
mpls traffic-eng
 interface GigabitEthernet0/0/0/0
 interface GigabitEthernet0/0/0/1
!
rsvp
 interface GigabitEthernet0/0/0/0
  bandwidth 10000          ! kb/s - matches the declared 10 Mb/s TE capacity
 interface GigabitEthernet0/0/0/1
  bandwidth 10000
!
router ospf 1
 area 0
  mpls traffic-eng
 mpls traffic-eng router-id Loopback0
!
! head-end(s) - stateful PCC with instantiation
mpls traffic-eng
 pce
  peer source ipv4 1.1.36.25
  peer ipv4 <ODL-IP>
  stateful-client
   instantiation
   delegation
   report
  !
 !
 auto-tunnel pcc
  tunnel-id min 1000 max 1999
!
```

To make R1→R3 traffic actually use the tunnel (needed for the "after" measurement), add
autoroute on the PCE-initiated tunnel if your release supports it, or a static route to the
receiver prefix via the `tunnel-te` interface once it exists.

Verification commands (capture them for F7/F8/F9):

```
show mpls traffic-eng pce peer
show mpls traffic-eng tunnels auto-tunnel pcc
show mpls traffic-eng tunnels brief
show mpls traffic-eng tunnels name SN3625-PCE-R1-R3-VIA-R2 detail
show rsvp session
show interfaces GigabitEthernet0/0/0/1 | include rate
```
