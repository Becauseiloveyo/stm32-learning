"""Produce reproducible 3-D-box projections (not mechanical drawings)."""
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['svg.fonttype']='none'
matplotlib.rcParams['svg.hashsalt']='hanger-layout-t12'
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch

GROUPS=[('hanger_bar','hanger bar','#45556c',False),('main','main unit','#087e8b',False),('battery','battery placeholder','#8b5fbf',False),('central_probe','central point','#dd7b1e',False),('reference_head','ambient reference','#299e6a',False),('rain_head','rain head','#c44d55',False),('charge_port','charging port','#9b3d15',False),('garment','garment proxy','#a6b3c3',False),('hook_clearance','hook clearance','#dc3545',True),('neck_clearance','neck clearance','#e47b30',True),('neighbor_clearance','neighbor clearance','#a64878',True),('contact_zone','contact target','#9a7400',True),('reference_vent','vent access','#238569',True),('rain_exposure','rain exposure','#b14558',True),('port_insertion','plug clearance','#9b3d15',True)]
def render(case,svg,png,title):
 fig,axes=plt.subplots(1,2,figsize=(15,6.8));fig.patch.set_facecolor('#f5f7fa')
 for ax,axis,name in zip(axes,[0,2],['Front: x / y','Side: z / y']):
  ax.set_facecolor('white')
  for key,_,color,dashed in GROUPS:
   b=case[key];xy=(b[axis],b[1]);width=b[axis+3]-b[axis];height=b[4]-b[1]
   ax.add_patch(Rectangle(xy,width,height,edgecolor=color,facecolor='none' if dashed else color,alpha=.85 if dashed else .35,linewidth=1.3,linestyle='--' if dashed else '-'))
  ax.autoscale_view();ax.set_aspect('equal',adjustable='box');ax.grid(alpha=.15);ax.set_title(name);ax.set_xlabel(('x' if axis==0 else 'z')+' (mm)');ax.set_ylabel('y (mm)')
 handles=[Patch(facecolor='none' if dash else col,edgecolor=col,alpha=.9 if dash else .5,linestyle='--' if dash else '-',label=lab) for _,lab,col,dash in GROUPS]
 fig.legend(handles=handles,loc='center right',bbox_to_anchor=(.995,.48),frameon=False,fontsize=9,title='Solid: entity   Dashed: clearance')
 fig.suptitle(title+' — DESIGN ASSUMPTIONS, NOT MEASURED',fontsize=14,weight='bold');fig.subplots_adjust(left=.07,right=.75,wspace=.35,top=.89,bottom=.11)
 fig.savefig(svg,metadata={'Date':None});fig.savefig(png,dpi=150,metadata={'Software':'matplotlib '+matplotlib.__version__});plt.close(fig)
