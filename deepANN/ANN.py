import numpy, time, cPickle, gzip

import os

import theano
import theano.tensor as T
from theano.tensor.shared_randomstreams import RandomStreams

from Activations import *
from Noise import *
from Regularization import *
from Reconstruction_cost import *

from Logistic_regression import LogisticRegression



class DenseLayer(object):
    """ Generalized Dense Layer class """
    def __init__(self, rng, theano_rng, inp, n_inp, n_out, act = "tanh", noise = None,
            wdreg = 'l2', spreg = 'l1', Winit = None, binit = None, maskinit = None,
            upmaskbool = False, tag = ''):
        """ This initialize a DenseLayer object:
            ***rng : numpy.random object
            ***theano_rng : theano shared random stream object
            ***inp : theano symbolic variable for the input of the layer (necessarly given)
            ***n_inp : nb of inputs
            ***n_out : nb of units
            ***act : activation [available in Activation.py]
            ***noise : noise [available in Noise.py]
            ***wdreg : weights decay regularization [available in Regularisation.py]
            ***spreg : sparsity of the code regularization [available in Regularisation.py]
            ***Winit : initial weights matrix (if None, randomly created)
            ***binit : initial bias vector (if None, 0s vector)
            ***maskinit : initial mask vector (if None: 1s vector; if 1: random -1/1s vector)
            ***tag : name of the layer
        """
        self.inp = inp
        # Random generator
        self.rng = rng
        self.theano_rng = theano_rng
        ## Number of inputs and outputs
        self.n_inp = n_inp
        self.n_out = n_out
        # Activation
        self.act = act
        self.act_fn = eval(self.act+"_act")
        # Noise
        self.noise = noise
        self.noise_fn = eval(self.noise+"_noise") if self.noise != None else None
        # Weight decay
        self.wdreg = wdreg
        self.wdreg_fn = eval(self.wdreg+"_reg")
        # Sparsity
        self.spreg = spreg
        self.spreg_fn = eval(self.spreg+"_spar")
        # Units specification
        self.one_sided = False if act in ['tanh','tanhnorm','softsign','arsinh','plc'] else True
        self.maskinit = maskinit
        if self.maskinit == 1:
            self.one_sided = False
        if self.maskinit != 1 and self.maskinit != None:
            if sum(self.maskinit.value) != self.maskinit.value.shape[0]:
                self.one_sided = False
        self.bounded = False if act in ['rectifier','softplus','arsinh','pascalv'] else 1.
        #special cases
        if act == 'tanhnorm':
            self.bounded = 1.759
        self.tag = tag
        #update mask for bbprop
        self.upmaskbool = upmaskbool
        #-----------------------------------------------------------------------------------
        allocW = False # to track allocation of the weights
        if Winit == None: # if no initial weights are given, do the random initialization
            wbound = numpy.sqrt(6./(n_inp+n_out))
            #@TO THINK: to have one different for specific activation:
            #here only valid for anti-symmetric f(-x)=-f(x) and [x~0 -> f(x)=x] activations.
            W_values = numpy.asarray( rng.uniform( low = -wbound, high = wbound, \
                                    size = (n_inp, n_out)), dtype = theano.config.floatX)
            self.W = theano.shared(value = W_values, name = 'W'+self.tag)
            allocW = True
        else:
            self.W = Winit
        
        allocb = False # to track allocation of the bias
        if binit == None: # if no initial bias are given, do the 0 initialization
            b_values = numpy.zeros((n_out,), dtype= theano.config.floatX)
            self.b = theano.shared(value= b_values, name = 'b'+self.tag)
            allocb = True
        else:
            self.b = binit
        
        allocmask = False # to track allocation of the mask
        if self.maskinit == None or self. maskinit == 1: # if no initial bias are given, do the 0 initialization
            if self.maskinit == 1:
                mask_values = numpy.asarray((rng.binomial(1,0.5,(n_out,))-0.5)*2, dtype= theano.config.floatX)
                allocmask = 'random'
            else:
                mask_values = numpy.ones((n_out,), dtype= theano.config.floatX)
                allocmask = 'ones'
            self.mask = theano.shared(value= mask_values, name = 'mask'+self.tag)
        else:
            self.mask = self.maskinit
        #-----------------------------------------------------------------------------------
        self.createout(None)
        # standardized attribute
        self.params = [self.W, self.b] # gradient descent params
        self.wd = self.wdreg_fn(self.W)
        self.sp = self.spreg_fn(self.out)
        if self.upmaskbool:
            self.updatemaskinit()
        # @TO DO: special update attibute (for bbprop, rectifier count, rescaling weigths..)
        #-----------------------------------------------------------------------------------
        # Print init
        print '\t\t\t**** DenseLayer.__init__ ****'
        print '\t\t\ttag = ', tag
        print '\t\t\tinp = ', inp
        print '\t\t\tn_inp = ', self.n_inp
        print '\t\t\tn_out = ', self.n_out
        print '\t\t\tact = ', self.act
        print '\t\t\tnoise = ', self.noise
        print '\t\t\tout = ', self.out
        print '\t\t\tparams (gradients) = ', self.params
        print '\t\t\twdreg (weigth decay) = ', self.wdreg
        print '\t\t\tspreg (sparsity) = ', self.spreg
        print '\t\t\tupmaskbool = ', self.upmaskbool
        print '\t\t\tmaskinit = ', self.maskinit
        print '\t\t\tallocW, allocb, allocmask = ', allocW, allocb, allocmask
        # @TO DO: special update attibute (for bbprop, rectifier count, rescaling weigths..)
    
    def createout(self, noise_lvl): #create output with a scalar noise parameter
        self.noise_lvl = noise_lvl
        if self.noise == None or self.noise_lvl == None or self.noise_lvl == 0.:
            self.lin = T.dot(self.inp, self.W) + self.b
            self.lin.name = 'lin'+self.tag
            self.out = self.mask * self.act_fn(self.lin)
            self.out.name = 'out'+self.tag
        else:
            self.lin = T.dot(self.noise_fn(self.theano_rng, self.inp, self.noise_lvl),\
                                self.W) + self.b
            self.lin.name = 'lin'+self.tag
            self.out = self.mask * self.act_fn(self.lin)
            self.out.name = 'out'+self.tag
        return self.out
    
    def updatemaskinit(self): #create update mask (used for bbprop)
        self.W_upmask = theano.shared(value= numpy.ones((self.n_inp, self.n_out),\
                        dtype= theano.config.floatX), name = 'W_fact'+self.tag)
        self.b_upmask = theano.shared(value= numpy.ones((self.n_out,),\
                        dtype= theano.config.floatX), name = 'b_fact'+self.tag)
        self.upmask = [self.W_upmask, self.b_upmask]
    
    def bbprop(self,outgrad): #symbolic bbprop declaration
        self.act_der = eval(self.act + '_der')
        self.lin_bbprop = outgrad * self.act_der(self.lin) * self.act_der(self.lin)
        self.dict_bbprop = {}
        self.dict_bbprop.update({self.b_upmask:T.sum(self.lin_bbprop,0)})
        self.dict_bbprop.update({self.W_upmask:T.dot(T.transpose(self.inp*self.inp),self.lin_bbprop)})
        return T.dot(self.lin_bbprop,T.transpose(self.W * self.W)),self.dict_bbprop
    
    def save(self,path):
        f = open(path+'W.pkl','w')
        cPickle.dump(self.W.value,f)
        f.close()
        print self.W, 'saved in %s'%(path+'W.pkl')
        f = open(path+'b.pkl','w')
        cPickle.dump(self.b.value,f)
        f.close()
        print self.b, 'saved in %s'%(path+'b.pkl')
        f = open(path+'mask.pkl','w')
        cPickle.dump(self.mask.value,f)
        f.close()
        print self.mask, 'saved in %s'%(path+'mask.pkl')
    
    def load(self,path):
        f = open(path+'W.pkl','r')
        self.W.value = cPickle.load(f)
        f.close()
        print self.W, 'loaded from %s'%(path+'W.pkl')
        f = open(path+'b.pkl','r')
        self.b.value = cPickle.load(f)
        f.close()
        print self.b, 'loaded from %s'%(path+'b.pkl')
        f = open(path+'mask.pkl','r')
        self.mask.value = cPickle.load(f)
        f.close()
        print self.mask, 'loaded from %s'%(path+'mask.pkl')
    
#def scaling_rectifierweigths(self):
#def moving_averageandvariances(self):
#def localcontrastnormalization(self):

#-----------------------------------------------------------------------------------

class SDAE(object):
    """ Generalized Denoising Autoencoder class"""
    def __init__(self, rng, theano_rng, depth , one_sided_in, inp = None, n_inp = 1, n_hid = 1,
            layertype = DenseLayer, act = "tanh" , noise = "binomial", tie = True, maskbool = None,
            n_out = 1, out = None, outtype = LogisticRegression, regularization = False,
            sparsity = False, wdreg = 'l2', spreg = 'l1', reconstruction_cost = 'cross_entropy', bbbool = None):
        """ This initialize a SDAE (Stacked Denoising Auto-Encoder) object:
            ***rng : numpy.random object
            ***theano_rng : theano shared random stream object
            ***depth : Number of hidden layers
            ***inp : theano symbolic variable for the input of the network (if None: created)
            ***n_inp : nb of inputs
            ***n_hid : nb of units per layer (or a vector of length depth if different sizes)
            ***layertype : Layerclass to be used (or a list if different)
            ***act : activation [available in Activation.py] (or a list if different)
            ***noise : noise [available in Noise.py] (or a list if different)
            ***tie : boolean, if true shared weights (or a list if different)
            ***maskbool : if None 1s mask, if 1 random mask (-1 1) (or a list if different)
            ***n_out : nb of outputs units
            ***out : theano symbolic variable for the output of the network (if None: created)
            ***outtype : outlayer class
            ***regularization : regularisation coefficient or false if not (or a list if different)
            ***spasity : sparsity coefficient or false if not (or a list if different)
            ***wdreg : weights decay regularization [available in Regularisation.py]
            ***spreg : sparsity of the code regularization [available in Regularisation.py]
            ***reconstruction_cost : cost of reconstruction (quadratic or cross-entropy or scaled)
        """
        self.one_sided_in = one_sided_in #very important for cross entropy reconstruction.
        self.inp = inp if inp != None else T.matrix('inp')
        self.out = out if out != None else T.ivector('out')
        # Random generator
        self.rng = rng
        self.theano_rng = theano_rng
        # Network size
        self.depth = depth
        self.n_inp = n_inp
        self.n_hid = self.__listify(n_hid,self.depth)
        self.n_out = n_out
        # Layer specific
        self.layertype = self.__listify(layertype,self.depth)
        self.act = self.__listify(act,self.depth)
        self.noise = self.__listify(noise,self.depth)
        self.tie = self.__listify(tie,self.depth)
        self.maskbool = self.__listify(maskbool,self.depth)
        # @Warning: bbprop in the tie weights case not trivial and not implemented!!!!
        for i in self.tie:
            if bbbool == True:
                assert i == False
        self.outtype = outtype
        self.regularization = self.__listify(regularization,self.depth+1) # +1 for supervised layer
        self.sparsity = self.__listify(sparsity,self.depth)
        self.reconstruction_cost = reconstruction_cost
        self.reconstruction_cost_fn = eval(reconstruction_cost+'_cost')
        self.wdreg = wdreg
        self.spreg = spreg
        self.bbbool = bbbool
        
        #for aux target init:
        self.aux = False
        self.aux_active = False
        self.auxdepth = None
        self.auxlayertype = None
        self.auxtarget = None
        self.aux_scaling = None
        self.aux_one_sided = None
        self.auxregularization = None
        self.auxn_out = None
        self.auxact = None
        self.aux_one_sided = None
        self.auxwdreg = None
        
        #hard coded list paramters for saving and loading
        self.paramsinitkeys = ['depth','one_sided_in','n_inp','n_hid','layertype',\
                        'act','noise','tie','maskbool','n_out','outtype','regularization',\
                        'sparsity','wdreg','spreg','reconstruction_cost','bbbool']
        self.paramscurrentkeys = ['mode','depth_min','depth_max','aux','noise_lvl',\
                            'update_type','lr','sup_scaling','unsup_scaling',\
                            'hessscal']
        self.paramsauxkeys = ['auxdepth','auxn_out','aux_scaling','auxregularization','auxlayertype',\
                            'auxact','aux_one_sided','auxwdreg','auxwdreg']
        #first init by default
        self.ModeMixte(self.depth)
    
    def __listify(self, x, length):
        if type(x) is list:
            assert len(x) == length
            return x
        else:
            return [x]*length
    
    def __redefinemodel(self,direction,depth_max,depth_min=0,noise_lvl=0.,update_type = 'global'):
        """ This function redefine the SDAE or the supervised model (i.e the added noise level
            and the last encoding layer until which to propagate)
            ***direction : 1 for encoding path, 0 for supervised layer, -1 for decoding path
            ***depth_max : last encoding layer (before decoding) or last input to supervised layer
            ***depth_min : layer at which you add noise in the input
                        therefore also the last decoding layer or first input to supervised layer
            ***noise_lvl : scalar from 0 to 1 specifying the amount of noise to add
            ***update_type : local (depth_max or outlayer if supervised),
                          global (from depth_min to depth_max or the whole network if supervised)
                          [only local for supervised with concatenated input]
        """
        
        # Encoding and decoding declaration
        if direction != 0:
            
            allocbool = False # this is used for the first declaration
            # different init for decoding and encoding:
            if direction == 1: #encoding
                # Inputs
                tmp_n_inp = self.n_inp
                tmp_inp = self.inp
                # Specifics to encoding path
                text = 'Encoding'
                tag = 'enc'
                if not hasattr(self,'layers'): #if first declaration
                    allocbool = True
                    self.layers = [None]*self.depth
                tmp_layers = self.layers
                # declaration loop iterator
                itera = xrange(0,depth_max)
            else:
                # Inputs
                tmp_n_inp = self.layers[depth_max-1].n_out #need an encoding path...
                tmp_inp = self.layers[depth_max-1].out
                # Specifics to decoding path
                text = 'Decoding'
                tag = 'dec'
                if not hasattr(self,'layersdec'): #if first declaration
                    allocbool = True
                    self.layersdec = [None]*self.depth
                tmp_layers = self.layersdec
                # declaration loop iterator
                itera = xrange(depth_max-1,depth_min-1,-1)
            
            # Stacking layers
            for i in itera:
                if direction == 1:
                    tmp_n_out = self.n_hid[i]
                else:
                    tmp_n_out = self.layers[i].n_inp
                print '\t\t---- ',text,' layer #%s ----'%(i+1)
                if allocbool:
                    Wtmp = None
                    btmp = None
                    masktmp = self.maskbool[i]
                else:
                    Wtmp = tmp_layers[i].W
                    btmp = tmp_layers[i].b
                    masktmp = tmp_layers[i].mask
                if direction == -1 and self.tie[i]:
                    Wtmp = self.layers[i].W.T
                    if i-1 >=0 :
                        masktmp = self.layers[i-1].mask
                    else:
                        masktmp = None
                print i
                tmp_layers[i] = self.layertype[i](self.rng, self.theano_rng,tmp_inp,tmp_n_inp,
                                    tmp_n_out, self.act[i], noise = self.noise[i],
                                    Winit = Wtmp, binit = btmp, maskinit = masktmp, wdreg = self.wdreg,
                                    spreg =self.spreg, tag = tag + '%s'%(i+1), upmaskbool = self.bbbool)
                # If noise layer in encoding path, add the noise
                if direction == 1 and i==depth_min: 
                    tmp_layers[i].createout(noise_lvl)
                
                # getting the right parameters for updates
                #-----------------------------------------------------------------------------------
                # Global update
                if update_type == 'global' and i >= depth_min:
                    if direction == 1 or not(self.tie[i]):
                        self.params += tmp_layers[i].params
                        if self.regularization[i]:
                            self.wd += [self.regularization[i]*tmp_layers[i].wd]
                    else:
                        self.params += [tmp_layers[i].params[-1]]
                    if self.bbbool:
                        self.upmask += tmp_layers[i].upmask
                    if direction == 1 and self.sparsity[i]:
                        self.sp += [self.sparsity[i]*tmp_layers[i].sp]
                    if direction == -1 and i > depth_min and self.sparsity[i-1]:
                        self.sp += [self.sparsity[i-1]*tmp_layers[i].sp]
                        #@TO THINK: is this obvious?
                        #not the same code in encoding and decoding but do we want the
                        #same sparsity in the codes...?
                        #no need to do it for depth_min in decoding (target already sparse)
                #-----------------------------------------------------------------------------------
                # Local update
                if (update_type == 'local' or update_type =='special') and i == depth_max-1 and self.mode != 'Sup' and\
                        (self.mode != 'Aux' or self.update_type == 'special'):
                    if direction == 1 or not(self.tie[i]):
                        self.params += tmp_layers[i].params
                        if self.regularization[i]:
                            self.wd += [self.regularization[i]*tmp_layers[i].wd]
                    if direction == 1 and self.sparsity[i]:
                        self.sp += [self.sparsity[i]*tmp_layers[i].sp]
                    if self.bbbool:
                        self.upmask += tmp_layers[i].upmask
                #-----------------------------------------------------------------------------------
                # New input for next iteration in the loop
                tmp_inp = tmp_layers[i].out
                tmp_n_inp = tmp_layers[i].n_out
        
        # outlayer declaration
        if direction == 0:
            if depth_max == depth_min: #only one input at depth_max
                tmp_inp = self.layers[depth_max-1].out
                tmp_n_inp = self.n_hid[depth_max-1]
            else: #concatenate layers from depth_min to depth_max
                assert update_type == 'local' #only local update allowed
                tmp_list = [1] #first arg of T.join()
                tmp_n_inp = 0
                if depth_min==-1:
                    tmp_list += [self.inp] #take the original input
                    depth_min = 0
                    tmp_n_inp += self.n_inp
                tmp_list += [self.layers[j].out for j in xrange(depth_min,depth_max)]
                #concatenate inputs
                tmp_inp = T.join(*tmp_list)
                tmp_n_inp += numpy.sum(self.n_hid[depth_min:depth_max])
            print '\t---- Output Layer ----'
            Winit = None
            binit = None
            if hasattr(self,'outlayer') and self.outlayerdepth == (depth_max,depth_min):
                Winit = self.outlayer.W
                binit = self.outlayer.b
            self.outlayer = self.outtype(self.rng,tmp_inp, tmp_n_inp, self.n_out,self.wdreg,\
                                            self.bbbool,Winit = Winit,binit = binit)
            self.outlayerdepth = (depth_max,depth_min)
            self.params += self.outlayer.params
            if self.bbbool:
                self.upmask += self.outlayer.upmask
            if self.regularization[-1]:
                self.wd += [self.regularization[-1]*self.outlayer.wd]
    
    def __definecost(self):
        """ This initialize the symbolic cost, grad and updates"""
        
        if self.mode == 'Unsup' or self.mode == "Mixte":
            if self.depth_min == 0:
                in_sided = self.one_sided_in
                in_bounded = 1.
            else:
                in_sided = self.layers[self.depth_min-1].one_sided
                in_bounded = self.layers[self.depth_min-1].bounded
            out_bounded = self.layersdec[self.depth_min].bounded
            out_sided = self.layersdec[self.depth_min].one_sided
            
            self.unsup_cost = self.reconstruction_cost_fn( self.layers[self.depth_min].inp,\
                        self.layersdec[self.depth_min].out, self.layersdec[self.depth_min].lin,\
                        in_sided,out_sided,in_bounded,out_bounded,self.layersdec[self.depth_min].act)
            self.unsup_cost[0][0] = self.unsup_scaling * self.unsup_cost[0][0]
            self.unsup_cost[1] = self.unsup_scaling * self.unsup_cost[1]
        else:
            self.unsup_cost = [[],[]]
        
        wdaux = []
        paramsaux = []
        if self.aux and self.aux_active:
            if (self.update_type != 'special' or self.depth+self.auxdepth == self.depth_max):
                paramsaux += self.auxlayer.params
                if self.auxregularization:
                    wdaux += [self.auxregularization*self.auxlayer.wd]
            self.aux_cost = self.reconstruction_cost_fn( self.auxtarget, self.auxlayer.out,\
                        self.auxlayer.lin,self.aux_one_sided,self.auxlayer.one_sided,\
                        1.,self.auxlayer.bounded,self.auxlayer.act)
            self.aux_cost[0][0] = self.aux_scaling * self.aux_cost[0][0]
            self.aux_cost[1] = self.aux_scaling * self.aux_cost[1]
        else:
            self.aux_cost = [[],[]]
        
        if self.mode == 'Sup':
            self.cost = sum([self.sup_scaling * self.outlayer.cost(self.out)] +\
                                    self.aux_cost[0] + self.wd + wdaux + self.sp)
        if self.mode == 'Unsup':
            self.cost = sum(self.unsup_cost[0] + self.aux_cost[0] + self.wd + wdaux + self.sp)
        if self.mode == 'Mixte':
            self.cost = sum(self.unsup_cost[0] + [self.sup_scaling * self.outlayer.cost(self.out)] +\
                                    self.aux_cost[0] + self.wd + wdaux + self.sp)
        if self.mode == 'Aux':
            self.cost = sum(self.aux_cost[0] + self.wd + wdaux + self.sp)
        self.grad = T.grad(self.cost,self.params+paramsaux)
        self.updates = dict((p, p - self.lr * g) for p, g in zip(self.params+paramsaux, self.grad))
    
    def __definebbprop(self):
        ParamHess = {}
        bb_tmp = 0
        if self.mode != 'Aux':
            if self.mode == 'Unsup' or self.mode == "Mixte":
                bb_tmp += self.unsup_cost[1]
                for i in xrange(self.depth_min,self.depth_max,1):
                    if self.aux and i == self.depth - self.auxdepth:
                        bb_tmp2, hess_tmp2 = self.auxlayer.bbprop(self.aux_cost[1])
                        bb_tmp += bb_tmp2
                        ParamHess.update(hess_tmp2)
                    bb_tmp, hess_tmp = self.layersdec[i].bbprop(bb_tmp)
                    ParamHess.update(hess_tmp)
            if self.mode == 'Sup' or self.mode == "Mixte":
                bb_tmp2, hess_tmp2 = self.outlayer.bbprop()
                bb_tmp += bb_tmp2
                ParamHess.update(hess_tmp2)
            if self.mode == 'Sup':
                if self.update_type=='global':
                    itera = xrange(self.depth_max-1,-1,-1)
                else:
                    itera = []
            if self.mode == 'Mixte' or self.mode == 'Unsup':
                itera = xrange(self.depth_max-1,self.depth_min-1,-1)
            for i in itera:
                if self.aux and i == self.depth + self.auxdepth - 1:
                    bb_tmp2, hess_tmp2 = self.auxlayer.bbprop(self.aux_cost[1])
                    bb_tmp += bb_tmp2
                    ParamHess.update(hess_tmp2)
                bb_tmp, hess_tmp = self.layers[i].bbprop(bb_tmp)
                ParamHess.update(hess_tmp)
            #auxconnected to first layer?
            if self.aux and self.depth + self.auxdepth == 0:
                bb_tmp2, hess_tmp2 = self.auxlayer.bbprop(self.aux_cost[1])
                ParamHess.update(hess_tmp2)
        else:
            assert self.aux_active
            bb_tmp, hess_tmp = self.auxlayer.bbprop(self.aux_cost[1])
            ParamHess.update(hess_tmp)
            if self.update_type=='global':
                if self.auxdepth>0:
                    for i in xrange(self.depth - self.auxdepth,self.depth_max,1):
                        bb_tmp, hess_tmp = self.layersdec[i].bbprop(bb_tmp)
                        ParamHess.update(hess_tmp)
                    for i in xrange(self.depth_max-1,-1,-1):
                        bb_tmp, hess_tmp = self.layers[i].bbprop(bb_tmp)
                        ParamHess.update(hess_tmp)
                else:
                    for i in xrange(self.depth-1+self.auxdepth,-1,-1):
                        bb_tmp, hess_tmp = self.layers[i].bbprop(bb_tmp)
                        ParamHess.update(hess_tmp)
        for p in ParamHess.keys():
            ParamHess[p]= self.hessscal / (ParamHess[p] + self.hessscal)
        paramsaux = []
        upmaskaux = []
        if self.aux and (self.update_type != 'special' or self.auxdepth == 0):
            paramsaux += self.auxlayer.params
            if self.bbbool:
                upmaskaux += self.auxlayer.upmask
        self.specialupdates.update(ParamHess)
        self.updates = dict((p, p - self.lr * m * g) for p, m, g in \
                                        zip(self.params+paramsaux, self.upmask+upmaskaux, self.grad))
    
    def __monitorfunction(self):
        inp1 = [self.inp]
        inp2 = [self.inp]
        if self.mode != 'Unsup':
            inp2 += [self.out]
        if self.aux:
            inp2 += [self.auxtarget]
        self.representation = [None] * (self.depth+1)
        self.activation = [None] * (self.depth+1)
        self.representationdec = [None] * self.depth
        self.activationdec = [None] * self.depth
        self.auxrepresentation = None
        self.auxactivation = None
        for i in range(self.depth_max):
            self.representation[i] = theano.function(inp1,self.layers[i].out)
            self.activation[i] = theano.function(inp1,self.layers[i].lin)
        if self.mode != 'Sup':
            for i in xrange(self.depth_max-1,self.depth_min-1,-1):
                self.representationdec[i] = theano.function(inp1,self.layersdec[i].out)
                self.activationdec[i] = theano.function(inp1,self.layersdec[i].lin)
        if self.mode != 'Unsup':
            self.representation[-1] = theano.function(inp1,self.outlayer.out)
            self.activation[-1] = theano.function(inp1,self.outlayer.lin)
        if self.aux:
            self.auxrepresentation = theano.function(inp1,self.auxlayer.out)
            self.auxactivation = theano.function(inp1,self.auxlayer.lin)
        self.compute_cost = theano.function(inp2,self.cost)
        self.compute_gradient = theano.function(inp2,self.grad)
        if self.mode != 'Unsup':
            self.error = theano.function(inp2,self.outlayer.errors(self.out))
    
    def __checkauxactive(self):
        if self.auxdepth>0:
            if self.mode == 'Sup':
                return False
            else:
                if (self.depth_max > self.depth - self.auxdepth) and (self.depth_min <= self.depth - self.auxdepth):
                    return True
                else:
                    return False
        else:
            if (self.depth_max >= self.depth + self.auxdepth):
                return True
            else:
                return False
    
    def auxiliary(self, init, auxdepth, auxn_out, aux_scaling=1., auxregularization = False,
            auxlayertype = DenseLayer, auxact = 'sigmoid', aux_one_sided = True,
            auxwdreg = 'l2', Wtmp = None, btmp = None,afficheb = True):
        """ This initialize an auxiliary target in the network only used in Mixte or Unsup Mode
            ***init : if 1 initialize the auxtarget, if 0 delete previous auxtarget
            ***auxlayer : a layer object already initialized with the good input given
            ***aux_one_sided : true if the auxtarget is one_sided
            ***auxregularization : weights decay regularisation value for the aux layer
            ***aux_scaling : scaling factor to equilibrate with unsup_cost
            @TO THINK: should I divide by the number of output neurons to make them comparable?
        """
        if init: #init side
            self.aux = True
            self.auxdepth = auxdepth
            self.aux_active = self.__checkauxactive()
            self.auxlayertype = auxlayertype
            self.auxtarget = T.matrix('auxtarget')
            self.aux_scaling = aux_scaling
            self.aux_one_sided = aux_one_sided
            self.auxregularization = auxregularization
            self.auxn_out = auxn_out
            self.auxact = auxact
            self.aux_one_sided = aux_one_sided
            self.auxwdreg = auxwdreg
            if auxdepth <= 0:
                tmp_inp = self.layers[auxdepth-1].out
                tmp_n_inp = self.layers[auxdepth-1].n_out
            else:
                tmp_inp = self.layersdec[-auxdepth].out
                tmp_n_inp = self.layersdec[-auxdepth].n_out
            print '\t---- Auxiliary Layer ----'
            self.auxlayer = self.auxlayertype(self.rng, self.theano_rng,tmp_inp,tmp_n_inp,auxn_out,
                                            auxact, noise = None, Winit = Wtmp, binit = btmp, wdreg = auxwdreg,
                                            spreg = 'l1', tag = 'aux' + '%s'%(auxdepth),upmaskbool = self.bbbool)
        else: #delete side
            assert self.mode != 'Aux'
            self.aux = False
            self.aux_active = False
            self.auxdepth = None
            self.auxlayertype = None
            self.auxtarget = None
            self.aux_scaling = None
            self.aux_one_sided = None
            self.auxregularization = None
            self.auxn_out = None
            self.auxact = None
            self.aux_one_sided = None
            self.auxwdreg = None
        #to redefine properly the function
        self.__definecost()
        self.__monitorfunction()
        if self.bbbool:
            self.__definebbprop()
        if afficheb:
            self.afficher()
    
    def trainfunctionbatch(self,data,datal=None,aux = None,batchsize = 10):
        # compute number of minibatches for training
        if type(data) is list:
            n_max = data[0].value.shape[0] / batchsize
        else:
            n_max = data.value.shape[0] / batchsize
        givens = {}
        index = T.lscalar()    # index to a [mini]batch
        if self.aux and self.aux_active:
            if type(aux) is list:
                givens.update({self.auxtarget:\
                    T.cast(aux[0][index*batchsize:(index+1)*batchsize]/aux[1]+aux[2],aux[3])})
            else:
                givens.update({self.auxtarget:aux[index*batchsize:(index+1)*batchsize]})
        if self.mode == 'Sup' or self.mode == 'Mixte':
            if type(datal) is list:
                givens.update({self.out:\
                    T.cast(datal[0][index*batchsize:(index+1)*batchsize]/datal[1]+datal[2],datal[3])})
            else:
                givens.update({self.out:datal[index*batchsize:(index+1)*batchsize]})
        if type(data) is list:
            givens.update({self.inp:\
                T.cast(data[0][index*batchsize:(index+1)*batchsize]/data[1]+data[2],data[3])})
        else:
            givens.update({self.inp:data[index*batchsize:(index+1)*batchsize]})
        # allocate symbolic variables for the data
        trainfunc = theano.function([index], self.cost, updates = self.updates, givens = givens)
        return trainfunc, n_max
    
    def errorfunction(self,data,datal,batchsize = 1000):
        if type(data) is list:
            n_max = data[0].value.shape[0] / batchsize
            assert  n_max*batchsize == data[0].value.shape[0]
        else:
            n_max = data.value.shape[0] / batchsize
            assert  n_max*batchsize == data.value.shape[0]
        givens = {}
        index = T.lscalar()    # index to a [mini]batch
        if self.mode != 'Unsup':
            if type(data) is list:
                givens.update({self.inp:\
                    T.cast(data[0][index*batchsize:(index+1)*batchsize]/data[1]+data[2],data[3])})
            else:
                givens.update({self.inp:data[index*batchsize:(index+1)*batchsize]})
            if type(datal) is list:
                givens.update({self.out:\
                    T.cast(datal[0][index*batchsize:(index+1)*batchsize]/datal[1]+datal[2],datal[3])})
            else:
                givens.update({self.out:datal[index*batchsize:(index+1)*batchsize]})
            # allocate symbolic variables for the data
            func = theano.function([index], self.outlayer.errors(self.out), givens = givens)
            def func2():
                error = 0
                for i in range(n_max):
                    error += func(i)
                return error / float(n_max)
            return func2
        else:
            return False
    
    def costfunction(self,data,datal=None,aux=None, batchsize = 1000):
        if type(data) is list:
            n_max = data[0].value.shape[0] / batchsize
            assert  n_max*batchsize == data[0].value.shape[0]
        else:
            n_max = data.value.shape[0] / batchsize
            assert  n_max*batchsize == data.value.shape[0]
        givens = {}
        index = T.lscalar()    # index to a [mini]batch
        if self.aux and self.aux_active:
            if type(aux) is list:
                givens.update({self.auxtarget:\
                    T.cast(aux[0][index*batchsize:(index+1)*batchsize]/aux[1]+aux[2],aux[3])})
            else:
                givens.update({self.auxtarget:aux[index*batchsize:(index+1)*batchsize]})
        if self.mode == 'Sup' or self.mode == 'Mixte':
            if type(datal) is list:
                givens.update({self.out:\
                    T.cast(datal[0][index*batchsize:(index+1)*batchsize]/datal[1]+datal[2],datal[3])})
            else:
                givens.update({self.out:datal[index*batchsize:(index+1)*batchsize]})
        if type(data) is list:
            givens.update({self.inp:\
                T.cast(data[0][index*batchsize:(index+1)*batchsize]/data[1]+data[2],data[3])})
        else:
            givens.update({self.inp:data[index*batchsize:(index+1)*batchsize]})
        # allocate symbolic variables for the data
        func = theano.function([index], self.cost, updates = self.updates, givens = givens)
        def func2():
            cost = 0
            for i in range(n_max):
                cost += func(i)
            return cost / float(n_max)
        return func2
    
    def save(self,fname):
        paramsinit = dict((i,self.__dict__[i]) for i in self.paramsinitkeys)
        paramscurrent = dict((i,self.__dict__[i]) for i in self.paramscurrentkeys)
        paramsaux = dict((i,self.__dict__[i]) for i in self.paramsauxkeys)
        f = open(fname+'/params.pkl','w')
        cPickle.dump(paramsinit,f)
        cPickle.dump(paramscurrent,f)
        cPickle.dump(paramsaux,f)
        f.close()
        print 'params.pkl saved in %s'%fname
        
        for i in range(self.depth):
            self.layers[i].save(fname+'/Layer'+str(i+1)+'_')
            if not self.tie[i]:
                self.layersdec[i].save(fname+'/Layerdec'+str(i+1)+'_')
        if self.auxlayer != None:
            self.auxlayer.save(fname+'/Layeraux_')
        self.outlayer.save(fname+'/Layerout_')
        print 'saved in %s'%fname
    
    def load(self,fname):
        f = open(fname+'/params.pkl','r')
        paramsinit = cPickle.load(f)
        paramscurrent = cPickle.load(f)
        paramsaux = cPickle.load(f)
        f.close()
        print 'params.pkl loaded in %s'%fname
        #reload base model-----------------------
        tmp = False
        for i in self.paramsinitkeys:
            if self.__dict__[i] != paramsinit[i]:
                tmp = True
        if tmp:
            del self.layers
            del self.layersdec
            paramsinit.update({'inp' : self.inp, 'out' : self.out,\
                                'rng':self.rng,'theano_rng':self.theano_rng})
            self.__init__(**paramsinit)
        #reload auxiliary layer------------------
        tmp = False
        for i in self.paramsauxkeys:
            if self.__dict__[i] != paramsaux[i]:
                tmp = True
        if tmp:
            self.auxiliary(1,**paramsaux)
        #reloadmode-----------------------------
        tmp = False
        for i in self.paramscurrentkeys:
            if self.__dict__[i] != paramscurrent[i]:
                tmp = True
        if tmp:
            self.aux = paramscurrent.pop('aux')
            if paramscurrent['mode'] == 'Sup':
                paramscurrent.pop('mode')
                paramscurrent.pop('noise_lvl')
                paramscurrent.pop('unsup_scaling')
                self.ModeSup(**paramscurrent)
            if paramscurrent['mode'] == 'Unsup':
                paramscurrent.pop('mode')
                paramscurrent.pop('sup_scaling')
                self.ModeUnsup(**paramscurrent)
            if paramscurrent['mode'] == 'Mixte':
                paramscurrent.pop('mode')
                self.ModeMixte(**paramscurrent)
            if paramscurrent['mode'] == 'Aux':
                paramscurrent.pop('mode')
                paramscurrent.pop('noise_lvl')
                paramscurrent.pop('unsup_scaling')
                paramscurrent.pop('sup_scaling')
                paramscurrent.pop('depth_min')
                self.ModeAux(**paramscurrent)
        for i in range(self.depth):
            self.layers[i].load(fname+'/Layer'+str(i+1)+'_')
            if not self.tie[i]:
                self.layersdec[i].load(fname+'/Layerdec'+str(i+1)+'_')
        if self.auxlayer != None:
            self.auxlayer.load(fname+'/Layeraux_')
        self.outlayer.load(fname+'/Layerout_')
        print 'loaded from %s'%fname
    
    def ModeMixte(self, depth_max, depth_min=0, noise_lvl=None, update_type = 'global',
                    lr = 0.1, sup_scaling = 1., unsup_scaling = 1., hessscal = 0.001):
        # @to change when equilibrium optimizer won't cycle sup_scaling = 0.5, unsup_scaling =0.5
        self.mode = 'Mixte'
        self.lr = lr
        self.sup_scaling = sup_scaling
        self.unsup_scaling = unsup_scaling
        self.hessscal = hessscal if self.bbbool else None
        self.update_type = update_type
        self.depth_max = depth_max
        self.depth_min = depth_min
        self.noise_lvl = noise_lvl
        # always to do for model redefining
        
        self.params = []
        self.upmask = []
        self.specialupdates = {}
        self.wd = []
        self.sp = []
        
        self.__redefinemodel(1, depth_max,depth_min,noise_lvl,update_type)
        self.__redefinemodel(0, depth_max,depth_max)
        self.__redefinemodel(-1, depth_max,depth_min,update_type=update_type)
        if self.aux:
            self.aux_active = self.__checkauxactive()
            if self.aux_active:
                self.auxiliary(1, auxdepth = self.auxdepth, auxn_out = self.auxn_out,
                        aux_scaling = self.aux_scaling, auxregularization = self.auxregularization,
                        auxlayertype = self.auxlayertype, auxact = self.auxact,
                        aux_one_sided = self.aux_one_sided,auxwdreg = self.auxwdreg,
                        Wtmp = self.auxlayer.W, btmp = self.auxlayer.b,afficheb = False)
        self.__definecost()
        if self.bbbool:
            self.__definebbprop()
        self.__monitorfunction()
        self.afficher()
    
    def ModeUnsup(self,depth_max,depth_min=0,noise_lvl=None,update_type = 'global',
                        lr = 0.1, unsup_scaling = 1., hessscal = 0.001):
        self.mode = 'Unsup'
        self.lr = lr
        self.sup_scaling = None
        self.unsup_scaling = unsup_scaling
        self.hessscal = hessscal if self.bbbool else None
        self.update_type = update_type
        self.depth_max = depth_max
        self.depth_min = depth_min
        self.noise_lvl = noise_lvl
        
        self.params = []
        self.upmask = []
        self.specialupdates = {}
        self.wd = []
        self.sp = []
        
        self.__redefinemodel(1, depth_max,depth_min,noise_lvl,update_type)
        self.__redefinemodel(-1, depth_max,depth_min,update_type=update_type)
        if self.aux:
            self.aux_active = self.__checkauxactive()
            if self.aux_active:
                self.auxiliary(1, auxdepth = self.auxdepth, auxn_out = self.auxn_out,
                        aux_scaling = self.aux_scaling, auxregularization = self.auxregularization,
                        auxlayertype = self.auxlayertype, auxact = self.auxact,
                        aux_one_sided = self.aux_one_sided,auxwdreg = self.auxwdreg,
                        Wtmp = self.auxlayer.W, btmp = self.auxlayer.b,afficheb = False)
        self.__definecost()
        if self.bbbool:
            self.__definebbprop()
        self.__monitorfunction()
        self.afficher()
    
    def ModeSup(self,depth_max,depth_min=0,update_type = 'global',lr = 0.1,
                    sup_scaling = 1., hessscal = 0.001):
        self.mode = 'Sup'
        self.lr = lr
        self.sup_scaling = sup_scaling
        self.unsup_scaling = None
        self.hessscal = hessscal if self.bbbool else None
        self.depth_max = depth_max
        self.depth_min = depth_min
        if depth_max != depth_min:
            update_type = 'local'
        self.update_type = update_type
        self.noise_lvl = None
        
        self.params = []
        self.upmask = []
        self.specialupdates = {}
        self.wd = []
        self.sp = []
        
        self.__redefinemodel(1, depth_max,0,None,update_type)
        self.__redefinemodel(0, depth_max,depth_min,update_type = update_type)
        if self.aux:
            self.aux_active = self.__checkauxactive()
            if self.aux_active:
                self.auxiliary(1, auxdepth = self.auxdepth, auxn_out = self.auxn_out,
                        aux_scaling = self.aux_scaling, auxregularization = self.auxregularization,
                        auxlayertype = self.auxlayertype, auxact = self.auxact,
                        aux_one_sided = self.aux_one_sided,auxwdreg = self.auxwdreg,
                        Wtmp = self.auxlayer.W, btmp = self.auxlayer.b,afficheb = False)
        self.__definecost()
        if self.bbbool:
            self.__definebbprop()
        self.__monitorfunction()
        self.afficher()
    
    def ModeAux(self,depth_max,update_type = 'global', lr = 0.1, hessscal = 0.001):
        self.depth_max = depth_max
        self.depth_min = 0
        self.aux_active = self.__checkauxactive()
        assert self.aux == True and self.aux_active == True
        self.mode = 'Aux'
        self.lr = lr
        self.sup_scaling = None
        self.unsup_scaling = None
        self.aux_scaling = 1.
        self.hessscal = hessscal if self.bbbool else None
        self.depth_max = depth_max
        self.update_type = update_type
        self.noise_lvl = None
        
        self.params = []
        self.upmask = []
        self.specialupdates = {}
        self.wd = []
        self.sp = []
        
        self.__redefinemodel(1, min(depth_max,self.depth + self.auxdepth),0,None,update_type)
        if self.auxdepth>0:
            self.__redefinemodel(-1, depth_max, self.depth - self.auxdepth,None,update_type)
        
        self.auxiliary(1, auxdepth = self.auxdepth, auxn_out = self.auxn_out,
                        aux_scaling = self.aux_scaling, auxregularization = self.auxregularization,
                        auxlayertype = self.auxlayertype, auxact = self.auxact,
                        aux_one_sided = self.aux_one_sided,auxwdreg = self.auxwdreg,
                        Wtmp = self.auxlayer.W, btmp = self.auxlayer.b,afficheb = False)
        self.__definecost()
        if self.bbbool:
            self.__definebbprop()
        self.__monitorfunction()
        self.afficher()
    
    def untie(self):
        for i in range(self.depth):
            if self.tie[i] == True:
                self.tie[i] = False
                self.layersdec[i].W = theano.shared(value = numpy.asarray(self.layers[i].W.value.T,\
                                                    dtype = theano.config.floatX),name = 'Wdec%s'%i)
                print 'layer %s untied, Warning, you need to redefine the mode'%i
    
    def afficher(self):
        paramsaux = []
        wdaux = []
        if self.aux and self.aux_active \
                    and (self.update_type != 'special' or self.depth+self.auxdepth == self.depth_max):
            paramsaux += self.auxlayer.params
            if self.auxregularization:
                wdaux += [self.auxregularization*self.auxlayer.wd]
        print '\t**** SDAE ****'
        print '\tdepth = ', self.depth
        print '\tmode = ', self.mode
        print '\tdepth_min, depth_max, update_type = ', self.depth_min,',', \
                                        self.depth_max,',', self.update_type
        print '\tinp, n_inp = ', self.inp ,',' , self.n_inp
        print '\tn_hid = ', self.n_hid
        print '\tlayertype = ', self.layertype
        print '\tact = ', self.act
        print '\tnoise, noise_lvl = ', self.noise,',', self.noise_lvl
        print '\ttie = ', self.tie
        print '\tmaskbool = ', self.maskbool
        print '\tn_out, outtype = ', self.n_out,',' , self.outtype
        print '\tlr = ', self.lr
        print '\tsup_scaling, unsup_scaling, aux_scaling  = ', self.sup_scaling, \
                                        self.unsup_scaling, self.aux_scaling
        print '\tregularization, wdreg  = ', self.regularization,',', self.wdreg
        print '\tsparsity, spreg = ', self.sparsity,',', self.spreg
        print '\tparams, wd, sp = ', self.params+paramsaux,',', self.wd+wdaux, ',', self.sp
        print '\tbbbool, hessscal = ', self.bbbool,',', self.hessscal
        print '\taux, auxtarget, aux_one_sided, auxdepth, auxregularization = ', \
                                self.aux, ',', self.auxtarget, ',', self.aux_one_sided, ',', \
                                self.auxdepth, ',', self.auxregularization
        print '\tauxact, auxn_out, auxwdreg = ', \
                                self.auxact, ',', self.auxn_out, ',', self.auxwdreg

if __name__ == '__main__':
    
    import pylearn.datasets.MNIST
    import copy
    
    
    dat = pylearn.datasets.MNIST.full()
    test = theano.shared(value = numpy.asarray((dat.test.x),dtype = theano.config.floatX),name='test')
    testl = T.cast(theano.shared(value = numpy.asarray(dat.test.y,dtype = theano.config.floatX),name='testl'),'int32')
    valid = theano.shared(value = numpy.asarray((dat.valid.x),dtype = theano.config.floatX),name='valid')
    validl = T.cast(theano.shared(value = numpy.asarray(dat.valid.y,dtype = theano.config.floatX),name='validl'),'int32')
    train = theano.shared(value = numpy.asarray((dat.train.x),dtype = theano.config.floatX),name='train')
    trainl = T.cast(theano.shared(value = numpy.asarray(dat.train.y,dtype = theano.config.floatX),name='trainl'),'int32')
    del dat
    
    
   
    a=SDAE(numpy.random,RandomStreams(),act='tanh',depth=3,one_sided_in=True,n_hid=1000,\
            regularization=False,sparsity=False,reconstruction_cost='cross_entropy',n_inp= 784,n_out=10,tie=False,maskbool=None)
    
    def training(a,train,trainl,test,testl,maxi = 2,b=False):
        g,n = a.trainfunctionbatch(train = train,trainl=trainl,batchsize=10)
        if b:
            f = a.errorfunction(data=test,datal=testl)
        else:
            f = a.costfunction(data=test,datal=testl)
        err = f()
        epoch = 0
        while epoch<=maxi:
            for i in range(n):
                print g(i), 'epoch:', epoch, 'err:',err , i
            epoch += 1
            err = f()
        train.value = train.value[numpy.random.permutation(train.value.shape[0]),:]
    
    a.ModeUnsup(1,0,0.25,'global',lr=0.01)
    training(a,train,trainl,test,testl,maxi = 1)
    
    a.ModeMixte(1,0,0.25,'global',lr=0.01,sup_scaling = 2.)
    training(a,train,trainl,test,testl,maxi = 5,b=True)
    
    a.ModeUnsup(2,1,0.25,'global',lr=0.01)
    training(a,train,trainl,test,testl,maxi = 2)
    
    a.ModeMixte(2,0,0.25,'global',lr=0.01,sup_scaling = 2.)
    training(a,train,trainl,test,testl,maxi = 5,b=True)
    
    a.ModeUnsup(3,2,0.25,'global',lr=0.01)
    training(a,train,trainl,test,testl,maxi = 3)
    
    a.ModeMixte(3,0,0.25,'global',lr=0.01,sup_scaling = 2.)
    training(a,train,trainl,test,testl,maxi = 5,b=True)
    
    a.ModeSup(3,3,'global',lr=0.05)
    training(a,train,trainl,test,testl,maxi = 50,b=True)
    
