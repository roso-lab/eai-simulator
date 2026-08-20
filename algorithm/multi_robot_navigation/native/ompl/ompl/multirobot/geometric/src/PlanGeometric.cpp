/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2008, Willow Garage, Inc.
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Willow Garage nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#include "ompl/multirobot/geometric/PlanGeometric.h"

ompl::multirobot::geometric::PlanGeometric::PlanGeometric(const PlanGeometric &plan) : ompl::multirobot::base::Plan(plan.si_)
{
    copyFrom(plan);
}

void ompl::multirobot::geometric::PlanGeometric::copyFrom(const PlanGeometric &other)
{
    paths_.resize(other.paths_.size());
    for (unsigned int i = 0; i < paths_.size(); ++i)
    {
        paths_[i] = std::make_shared<ompl::geometric::PathGeometric>(*other.paths_[i]);
    }
}

void ompl::multirobot::geometric::PlanGeometric::append(const ompl::geometric::PathGeometricPtr &path)
{
    paths_.push_back(std::make_shared<ompl::geometric::PathGeometric>(*path));
}

double ompl::multirobot::geometric::PlanGeometric::length() const
{
    double L = 0.0;
    for (unsigned int i = 0; i < paths_.size(); ++i)
        L += paths_[i]->length();
    return L;   
}

void ompl::multirobot::geometric::PlanGeometric::print(std::ostream &out, std::string prefix) const
{
    for (unsigned int i = 0; i < paths_.size(); ++i)
    {
        out << prefix << " " << i << std::endl;
        paths_[i]->print(out);
    }
}

void ompl::multirobot::geometric::PlanGeometric::printAsMatrix(std::ostream &out, std::string prefix) const
{
    for (unsigned int i = 0; i < paths_.size(); ++i)
    {
        out << prefix << " " << i << std::endl;
        paths_[i]->printAsMatrix(out);
    }
}
